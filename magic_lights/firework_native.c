#include "py/dynruntime.h"

#define FIREWORK_FRACTION_LENGTH 8
#define FIREWORK_FRACTION_MAXIMUM (1 << FIREWORK_FRACTION_LENGTH)

#define FIREWORK_EXPLOSION_DENSITY 50
#define FIREWORK_EXPLOSION_CREATION_PROBABILITY 1
#define FIREWORK_EXPLOSION_PARTICLES_LOWER_LIMIT 10
#define FIREWORK_EXPLOSION_PARTICLES_UPPER_LIMIT 50
#define FIREWORK_EXPLOSION_SPEED_MULTIPLIER 768

#define FIREWORK_PARTICLE_SPEED_UPPER_LIMIT 96
#define FIREWORK_PARTICLE_SIZE_UPPER_LIMIT 6

#define FIREWORK_PARTICLE_IGNITION_DURATION_MS 200
#define FIREWORK_PARTICLE_BURN_DURATION_MS 0
#define FIREWORK_PARTICLE_FADE_DURATION_SHIFT 11
#define FIREWORK_PARTICLE_FADE_DURATION_MS (1 << FIREWORK_PARTICLE_FADE_DURATION_SHIFT)

#define FIREWORK_COLOR(r, g, b) ((firework_color_t) {.red = (r), .green = (g), .blue = (b)})

#define FIREWORK_SCALE_COLOR(color, scale) \
    FIREWORK_COLOR(color.red *scale >> FIREWORK_FRACTION_LENGTH, \
        color.green * scale >> FIREWORK_FRACTION_LENGTH, \
        color.blue * scale >> FIREWORK_FRACTION_LENGTH)

#define FIREWORK_COLOR_TUPLE(color) \
    mp_obj_new_tuple( \
        3, ((mp_obj_t[]) {mp_obj_new_int(color.red), mp_obj_new_int(color.green), mp_obj_new_int(color.blue)}))

#define FIREWORK_PIXEL_TUPLE(pixel) \
    mp_obj_new_tuple(2, ((mp_obj_t[]) {mp_obj_new_int(pixel.position), FIREWORK_COLOR_TUPLE(pixel.color)}))

mp_obj_full_type_t mp_type_firework_engine;
mp_obj_full_type_t mp_type_firework_particle_queue_iter;
mp_obj_full_type_t mp_type_firework_pixel_iter;

mp_getiter_iternext_custom_t firework_particle_queue_iter_getiter_iternext;
mp_getiter_iternext_custom_t firework_pixel_iter_getiter_iternext;

typedef uint32_t xorshift32_state_t;

typedef struct _firework_color_t {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} firework_color_t;

typedef struct _firework_particle_t {
    xorshift32_state_t *xorshift32_state;
    int32_t position;
    int32_t speed;
    int32_t energy;
    uint32_t age_ms;
    firework_color_t color;
} firework_particle_t;

typedef struct _firework_engine_bounds_t {
    int32_t min;
    int32_t max;
} firework_engine_bounds_t;

typedef struct _firework_particle_queue_t {
    uint32_t head;
    uint32_t tail;
    uint32_t length;
    uint32_t capacity;
    firework_particle_t **items;
} firework_particle_queue_t;

typedef struct _firework_engine_t {
    xorshift32_state_t *xorshift32_state;
    firework_engine_bounds_t bounds;
    firework_particle_queue_t particles;
} firework_engine_t;

typedef struct _firework_pixel_t {
    int32_t position;
    firework_color_t color;
} firework_pixel_t;

typedef struct _firework_engine_obj_t {
    mp_obj_base_t base;
    xorshift32_state_t xorshift32_state;
    firework_engine_t engine;
} firework_engine_obj_t;

typedef struct _firework_particle_queue_iter_t {
    mp_obj_base_t base;
    uint32_t index;
    firework_particle_queue_t *queue;
} firework_particle_queue_iter_t;

typedef struct _firework_pixel_iter_t {
    mp_obj_base_t base;
    uint32_t particle_index;
    uint32_t particle_size;
    uint32_t pixel_index;
    firework_engine_bounds_t *bounds;
    firework_particle_queue_t *particles;
    firework_pixel_t *pixels;
} firework_pixel_iter_t;

typedef enum {
    FIREWORK_PARTICLE_PHASE_IGNITION,
    FIREWORK_PARTICLE_PHASE_BURN,
    FIREWORK_PARTICLE_PHASE_FADE,
    FIREWORK_PARTICLE_PHASE_DEAD
} firework_particle_phase_t;

static uint32_t xorshift32(xorshift32_state_t *state) {
    uint32_t x = *state;

    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;

    *state = x;
    return x;
}

static int32_t xorshift32_randint(xorshift32_state_t *state, int32_t min, int32_t max) {
    uint32_t range = (uint32_t) (max - min) + 1;
    uint32_t x = xorshift32(state) >> 16;
    return min + (int32_t) ((x * range) >> 16);
}

static firework_color_t firework_random_color(xorshift32_state_t *state) {
    return (firework_color_t) {.red = xorshift32_randint(state, 0, 255),
        .green = xorshift32_randint(state, 0, 255),
        .blue = xorshift32_randint(state, 0, 255)};
}

static void firework_particle_queue_init(firework_particle_queue_t *queue, uint32_t capacity) {
    queue->head = 0;
    queue->tail = 0;
    queue->length = 0;
    queue->capacity = capacity;
    queue->items = m_malloc(sizeof(firework_particle_t *) * capacity);
}

static bool firework_particle_queue_get(firework_particle_queue_t *queue, uint32_t i, firework_particle_t **particle) {
    if (i >= queue->length) {
        return false;
    }

    uint32_t j = queue->head + i;

    if (j >= queue->capacity) {
        j -= queue->capacity;
    }

    *particle = queue->items[j];
    return true;
}

static bool firework_particle_queue_full(const firework_particle_queue_t *queue) {
    return queue->length == queue->capacity;
}

static bool firework_particle_queue_push(firework_particle_queue_t *queue, firework_particle_t *particle) {
    if (firework_particle_queue_full(queue)) {
        return false;
    }

    uint32_t tail = queue->tail;

    queue->items[tail] = particle;
    tail = tail + 1;

    if (tail >= queue->capacity) {
        tail = 0;
    }

    queue->tail = tail;
    queue->length++;
    return true;
}

static bool firework_particle_queue_pop(firework_particle_queue_t *queue, firework_particle_t **particle) {
    if (queue->length == 0) {
        return false;
    }

    uint32_t head = queue->head;

    *particle = queue->items[head];
    head = head + 1;

    if (head >= queue->capacity) {
        head = 0;
    }

    queue->head = head;
    queue->length--;
    return true;
}

static mp_obj_t firework_particle_queue_iter_getiter(mp_obj_t self_in, mp_obj_iter_buf_t *iter_buf) {
    firework_particle_queue_iter_t *self = MP_OBJ_TO_PTR(self_in);
    self->index = 0;
    return self_in;
}

static mp_obj_t firework_particle_queue_iter_iternext(mp_obj_t self_in) {
    firework_particle_queue_iter_t *self = MP_OBJ_TO_PTR(self_in);
    firework_particle_t *particle;

    if (!firework_particle_queue_get(self->queue, self->index, &particle)) {
        return MP_OBJ_STOP_ITERATION;
    }

    self->index++;

    return mp_obj_new_tuple(4,
        ((mp_obj_t[]) {mp_obj_new_int(particle->position),
            mp_obj_new_int(particle->speed),
            mp_obj_new_int(particle->energy),
            mp_obj_new_int(particle->age_ms)}));
}

static void firework_particle_init(firework_particle_t *particle,
    xorshift32_state_t *xorshift32_state,
    int32_t position,
    int32_t max_speed,
    int32_t energy,
    firework_color_t color) {
    particle->xorshift32_state = xorshift32_state;
    particle->position = position;
    particle->speed = xorshift32_randint(xorshift32_state, -max_speed, max_speed);
    particle->energy = FIREWORK_FRACTION_MAXIMUM;
    particle->age_ms = 0;
    particle->color = color;
}

static firework_particle_phase_t firework_particle_phase(const firework_particle_t *particle) {
    int32_t age = particle->age_ms;

    if (age < FIREWORK_PARTICLE_IGNITION_DURATION_MS) {
        return FIREWORK_PARTICLE_PHASE_IGNITION;
    }

    age -= FIREWORK_PARTICLE_IGNITION_DURATION_MS;
    if (age < FIREWORK_PARTICLE_BURN_DURATION_MS) {
        return FIREWORK_PARTICLE_PHASE_BURN;
    }

    age -= FIREWORK_PARTICLE_BURN_DURATION_MS;
    if (age < FIREWORK_PARTICLE_FADE_DURATION_MS) {
        return FIREWORK_PARTICLE_PHASE_FADE;
    }

    return FIREWORK_PARTICLE_PHASE_DEAD;
}

static int32_t firework_particle_visibility(const firework_particle_t *particle, firework_particle_phase_t phase) {
    int32_t age = particle->age_ms;

    if (phase == FIREWORK_PARTICLE_PHASE_IGNITION) {
        return FIREWORK_FRACTION_MAXIMUM;
    } else if (phase == FIREWORK_PARTICLE_PHASE_BURN) {
        return FIREWORK_FRACTION_MAXIMUM;
    } else if (phase == FIREWORK_PARTICLE_PHASE_FADE) {
        return FIREWORK_FRACTION_MAXIMUM - (FIREWORK_FRACTION_MAXIMUM * (age - FIREWORK_PARTICLE_BURN_DURATION_MS -
                                                                            FIREWORK_PARTICLE_IGNITION_DURATION_MS) >>
                                               FIREWORK_PARTICLE_FADE_DURATION_SHIFT);
    } else {
        return 0;
    }
}

static void firework_particle_update(firework_particle_t *particle, uint32_t dt) {
    particle->age_ms += dt;
    particle->position += particle->speed * dt;
    particle->speed -= (particle->speed * dt) >> 9;
    particle->energy =
        (particle->energy * xorshift32_randint(particle->xorshift32_state, 230, 256)) >> FIREWORK_FRACTION_LENGTH;
}

static uint32_t firework_engine_bounds_length(const firework_engine_bounds_t *bounds) {
    return (uint32_t) (bounds->max - bounds->min + 1);
}

static void firework_engine_init(
    firework_engine_t *engine, xorshift32_state_t *xorshift32_state, firework_engine_bounds_t bounds) {
    engine->xorshift32_state = xorshift32_state;
    engine->bounds = bounds;
    firework_particle_queue_init(&engine->particles, firework_engine_bounds_length(&bounds));
}

static void firework_engine_ignite(firework_engine_t *engine, int32_t position, uint32_t particle_count) {
    uint32_t multiplier =
        (uint32_t) xorshift32_randint(engine->xorshift32_state, 0, FIREWORK_EXPLOSION_SPEED_MULTIPLIER);
    uint32_t speed_limit = (FIREWORK_PARTICLE_SPEED_UPPER_LIMIT * multiplier) >> FIREWORK_FRACTION_LENGTH;

    for (uint32_t i = 0; i < particle_count; i++) {
        int32_t max_speed = xorshift32_randint(engine->xorshift32_state, 0, speed_limit);
        firework_particle_t *particle = m_malloc(sizeof(firework_particle_t));
        firework_particle_init(particle,
            engine->xorshift32_state,
            position,
            max_speed,
            FIREWORK_FRACTION_MAXIMUM,
            firework_random_color(engine->xorshift32_state));

        if (firework_particle_queue_full(&engine->particles)) {
            firework_particle_t *out_particle;
            if (firework_particle_queue_pop(&engine->particles, &out_particle)) {
                m_free(out_particle);
            }
        }

        firework_particle_queue_push(&engine->particles, particle);
    }
}

static void firework_engine_ignite_default(firework_engine_t *engine) {
    int32_t position = xorshift32_randint(engine->xorshift32_state, engine->bounds.min, engine->bounds.max)
                       << FIREWORK_FRACTION_LENGTH;
    uint32_t particle_count = (uint32_t) xorshift32_randint(
        engine->xorshift32_state, FIREWORK_EXPLOSION_PARTICLES_LOWER_LIMIT, FIREWORK_EXPLOSION_PARTICLES_UPPER_LIMIT);

    firework_engine_ignite(engine, position, particle_count);
}

static void firework_engine_update(firework_engine_t *engine, uint32_t dt) {
    uint32_t bounds_length = firework_engine_bounds_length(&engine->bounds);

    for (uint32_t i = 0; i < bounds_length; i += FIREWORK_EXPLOSION_DENSITY) {
        if (xorshift32_randint(engine->xorshift32_state, 0, 99) < FIREWORK_EXPLOSION_CREATION_PROBABILITY) {
            firework_engine_ignite_default(engine);
        }
    }

    for (uint32_t i = 0; i < engine->particles.length; i++) {
        firework_particle_t *particle;
        firework_particle_queue_get(&engine->particles, i, &particle);
        firework_particle_update(particle, dt);
    }

    while (engine->particles.length) {
        firework_particle_t *particle;
        firework_particle_queue_get(&engine->particles, 0, &particle);

        if (firework_particle_phase(particle) == FIREWORK_PARTICLE_PHASE_DEAD) {
            if (firework_particle_queue_pop(&engine->particles, &particle)) {
                m_free(particle);
            }
        } else {
            break;
        }
    }
}

static void firework_render_particle(const firework_particle_t *particle,
    const firework_engine_bounds_t *bounds,
    firework_pixel_t *pixels,
    uint32_t *size) {
    int32_t particle_position = particle->position;
    firework_color_t color = particle->color;
    firework_particle_phase_t particle_phase = firework_particle_phase(particle);
    int32_t particle_visibility = firework_particle_visibility(particle, particle_phase);

    if (particle_phase == FIREWORK_PARTICLE_PHASE_IGNITION) {
        color = FIREWORK_COLOR(255, 255, 255);
    } else {
        int32_t scale = (particle->energy * particle_visibility) >> FIREWORK_FRACTION_LENGTH;
        color = FIREWORK_SCALE_COLOR(color, scale);
    }

    uint32_t particle_size =
        (particle_visibility * (FIREWORK_PARTICLE_SIZE_UPPER_LIMIT - 1)) >> FIREWORK_FRACTION_LENGTH;
    int32_t fractional = particle_position & (FIREWORK_FRACTION_MAXIMUM - 1);
    int32_t start_position = particle_position >> FIREWORK_FRACTION_LENGTH;

    *size = 0;

    for (int32_t i = 0; i <= particle_size; i++) {
        int32_t position = i + start_position;

        if (!(bounds->min <= position && position <= bounds->max)) {
            continue;
        }

        int32_t coverage;

        if (i == 0) {
            coverage = FIREWORK_FRACTION_MAXIMUM - fractional;
        } else if (i < particle_size) {
            coverage = FIREWORK_FRACTION_MAXIMUM;
        } else if (fractional > 0) {
            coverage = fractional;
        } else {
            break;
        }

        if (coverage != FIREWORK_FRACTION_MAXIMUM) {
            color = FIREWORK_SCALE_COLOR(color, coverage);
        }

        pixels[i].position = position;
        pixels[i].color = color;
        (*size)++;
    }
}

static mp_obj_t firework_pixel_iter_getiter(mp_obj_t self_in, mp_obj_iter_buf_t *iter_buf) {
    firework_pixel_iter_t *self = MP_OBJ_TO_PTR(self_in);
    self->particle_index = 0;
    self->particle_size = 0;
    self->pixel_index = 0;
    return self_in;
}

static mp_obj_t firework_pixel_iter_iternext(mp_obj_t self_in) {
    firework_pixel_iter_t *self = MP_OBJ_TO_PTR(self_in);

    if (self->pixel_index < self->particle_size) {
        firework_pixel_t pixel = self->pixels[self->pixel_index++];
        return FIREWORK_PIXEL_TUPLE(pixel);
    }

    firework_particle_t *particle;
    uint32_t particle_size;

    while (firework_particle_queue_get(self->particles, self->particle_index++, &particle)) {
        firework_render_particle(particle, self->bounds, self->pixels, &particle_size);

        if (particle_size == 0) {
            continue;
        }

        self->pixel_index = 1;
        self->particle_size = particle_size;

        firework_pixel_t pixel = self->pixels[0];
        return FIREWORK_PIXEL_TUPLE(pixel);
    }

    return MP_OBJ_STOP_ITERATION;
}

static mp_obj_t firework_render(mp_obj_t engine_in) {
    firework_engine_obj_t *engine_obj = MP_OBJ_TO_PTR(engine_in);
    firework_pixel_iter_t *pixels =
        mp_obj_malloc(firework_pixel_iter_t, (const mp_obj_type_t *) &mp_type_firework_pixel_iter);
    pixels->bounds = &engine_obj->engine.bounds;
    pixels->particles = &engine_obj->engine.particles;
    pixels->pixels = m_malloc(sizeof(firework_pixel_t) * FIREWORK_PARTICLE_SIZE_UPPER_LIMIT);
    return MP_OBJ_FROM_PTR(pixels);
}

static MP_DEFINE_CONST_FUN_OBJ_1(firework_render_obj, firework_render);

static mp_obj_t firework_engine_obj_make_new(
    const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args_in) {
    mp_arg_check_num(n_args, n_kw, 1, 2, false);

    mp_obj_t bounds_obj = args_in[0];
    mp_obj_t bounds_min = mp_load_attr(bounds_obj, MP_QSTR_min);
    mp_obj_t bounds_max = mp_load_attr(bounds_obj, MP_QSTR_max);

    firework_engine_bounds_t bounds = {.min = mp_obj_get_int(bounds_min), .max = mp_obj_get_int(bounds_max)};

    uint32_t xorshift32_state = 1;

    if (n_args > 1) {
        mp_obj_t seed_obj = args_in[1];
        xorshift32_state = mp_obj_get_int(seed_obj);
    }

    if (xorshift32_state == 0) {
        mp_raise_ValueError("seed must be greater than zero");
    }

    firework_engine_obj_t *self = mp_obj_malloc(firework_engine_obj_t, type);
    self->xorshift32_state = xorshift32_state;
    firework_engine_init(&self->engine, &self->xorshift32_state, bounds);

    return MP_OBJ_FROM_PTR(self);
}

static void firework_engine_obj_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }

    if (attr == MP_QSTR_particles) {
        firework_engine_obj_t *self = MP_OBJ_TO_PTR(self_in);
        firework_particle_queue_iter_t *particles = mp_obj_malloc(
            firework_particle_queue_iter_t, (const mp_obj_type_t *) &mp_type_firework_particle_queue_iter);
        particles->queue = &self->engine.particles;
        particles->index = 0;
        dest[0] = MP_OBJ_FROM_PTR(particles);
    } else {
        dest[1] = MP_OBJ_SENTINEL;
    }
}

static mp_obj_t firework_engine_obj_ignite(mp_obj_t self_in) {
    firework_engine_obj_t *self = MP_OBJ_TO_PTR(self_in);
    firework_engine_ignite_default(&self->engine);
    return mp_const_none;
}

static MP_DEFINE_CONST_FUN_OBJ_1(firework_engine_obj_ignite_obj, firework_engine_obj_ignite);

static mp_obj_t firework_engine_obj_update(mp_obj_t self_in, mp_obj_t dt_in) {
    firework_engine_obj_t *self = MP_OBJ_TO_PTR(self_in);
    uint32_t dt = mp_obj_get_int(dt_in);
    firework_engine_update(&self->engine, dt);
    return mp_const_none;
}

static MP_DEFINE_CONST_FUN_OBJ_2(firework_engine_obj_update_obj, firework_engine_obj_update);

mp_map_elem_t firework_engine_obj_locals_dict_table[2];
static MP_DEFINE_CONST_DICT(firework_engine_obj_locals_dict, firework_engine_obj_locals_dict_table);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY

    firework_particle_queue_iter_getiter_iternext.getiter = firework_particle_queue_iter_getiter;
    firework_particle_queue_iter_getiter_iternext.iternext = firework_particle_queue_iter_iternext;

    mp_type_firework_particle_queue_iter.base.type = (void *) &mp_type_type;
    mp_type_firework_particle_queue_iter.flags = MP_TYPE_FLAG_ITER_IS_CUSTOM;
    mp_type_firework_particle_queue_iter.name = MP_QSTR_ParticleQueueIterator;

    MP_OBJ_TYPE_SET_SLOT(
        &mp_type_firework_particle_queue_iter, iter, &firework_particle_queue_iter_getiter_iternext, 0);

    firework_pixel_iter_getiter_iternext.getiter = firework_pixel_iter_getiter;
    firework_pixel_iter_getiter_iternext.iternext = firework_pixel_iter_iternext;

    mp_type_firework_pixel_iter.base.type = (void *) &mp_type_type;
    mp_type_firework_pixel_iter.flags = MP_TYPE_FLAG_ITER_IS_CUSTOM;
    mp_type_firework_pixel_iter.name = MP_QSTR_PixelIterator;

    MP_OBJ_TYPE_SET_SLOT(&mp_type_firework_pixel_iter, iter, &firework_pixel_iter_getiter_iternext, 0);

    mp_type_firework_engine.base.type = (void *) &mp_type_type;
    mp_type_firework_engine.flags = MP_TYPE_FLAG_NONE;
    mp_type_firework_engine.name = MP_QSTR_Engine;

    MP_OBJ_TYPE_SET_SLOT(&mp_type_firework_engine, make_new, firework_engine_obj_make_new, 0);

    firework_engine_obj_locals_dict_table[0] =
        (mp_map_elem_t) {MP_OBJ_NEW_QSTR(MP_QSTR_ignite), MP_OBJ_FROM_PTR(&firework_engine_obj_ignite_obj)};

    firework_engine_obj_locals_dict_table[1] =
        (mp_map_elem_t) {MP_OBJ_NEW_QSTR(MP_QSTR_update), MP_OBJ_FROM_PTR(&firework_engine_obj_update_obj)};

    MP_OBJ_TYPE_SET_SLOT(&mp_type_firework_engine, locals_dict, (void *) &firework_engine_obj_locals_dict, 1);
    MP_OBJ_TYPE_SET_SLOT(&mp_type_firework_engine, attr, firework_engine_obj_attr, 2);

    mp_store_global(MP_QSTR_Engine, MP_OBJ_FROM_PTR(&mp_type_firework_engine));
    mp_store_global(MP_QSTR_render, MP_OBJ_FROM_PTR(&firework_render_obj));

    MP_DYNRUNTIME_INIT_EXIT
}
