// QtCar's Model S/X external Chromium window uses an EGL image from its X pixmap.
// On a PC without the car's DRI path, eglCreateImageKHR can fail and leave the media
// view black. Reuse the Model 3 software pixmap-to-texture fallback. This file has
// only the EGL/GL hooks: the 2019 CEF window and touch hooks do not apply here.
// Built by the 2026.8.3 patch profile and preloaded into QtCar only.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

// glibc >= 2.34 exports these with new versions the firmware's glibc doesn't have
__asm__(".symver dlsym,dlsym@GLIBC_2.2.5");
__asm__(".symver dlopen,dlopen@GLIBC_2.2.5");

#define EGL_NATIVE_PIXMAP_KHR 0x30B0
#define GL_TEXTURE_2D 0x0DE1
#define GL_TEXTURE_BINDING_2D 0x8069
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define REFRESH_MS 30
#define MAX_IMAGES 64

typedef void *EGLDisplay, *EGLContext, *EGLImageKHR, *EGLClientBuffer;
typedef int EGLint;
typedef unsigned int EGLenum, GLenum, GLuint, EGLBoolean;
typedef int GLint, GLsizei;
typedef void (*fnptr)(void);

// minimal XCB interface, resolved at runtime from libxcb.so.1
typedef struct { unsigned int sequence; } xcb_cookie_t;
typedef struct {
    uint8_t response_type, depth; uint16_t sequence; uint32_t length, visual; uint8_t pad[20];
} xcb_get_image_reply_t;
typedef struct {
    uint8_t response_type, depth; uint16_t sequence; uint32_t length, root;
    int16_t x, y; uint16_t width, height, border_width; uint8_t pad[2];
} xcb_get_geometry_reply_t;
static void *(*p_xcb_connect)(const char *, int *);
static int (*p_xcb_connection_has_error)(void *);
static xcb_cookie_t (*p_xcb_get_image)(void *, uint8_t, uint32_t, int16_t, int16_t, uint16_t, uint16_t, uint32_t);
static xcb_get_image_reply_t *(*p_xcb_get_image_reply)(void *, xcb_cookie_t, void **);
static uint8_t *(*p_xcb_get_image_data)(const xcb_get_image_reply_t *);
static int (*p_xcb_get_image_data_length)(const xcb_get_image_reply_t *);
static xcb_cookie_t (*p_xcb_get_geometry)(void *, uint32_t);
static xcb_get_geometry_reply_t *(*p_xcb_get_geometry_reply)(void *, xcb_cookie_t, void **);

struct image {
    int used;
    unsigned long pixmap;
    GLuint texture;
    int width, height, uploaded;
    long long last_refresh_ms;
};
static struct image images[MAX_IMAGES];
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void *xcb;  // connection
static int debug;
static int force_copy;

static EGLImageKHR (*real_create)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *);
static EGLBoolean (*real_destroy)(EGLDisplay, EGLImageKHR);
static void (*real_target)(GLenum, EGLImageKHR);
static void (*real_bind)(GLenum, GLuint);
static void (*real_get_integerv)(GLenum, GLint *);
static void (*real_tex_image)(GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *);
static void (*real_tex_sub_image)(GLenum, GLint, GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, const void *);

#define LOG(...) do { if (debug) fprintf(stderr, "egl_pixmap_shim: " __VA_ARGS__); } while (0)

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000LL + ts.tv_nsec / 1000000;
}

static int xcb_ready(void) {
    if (xcb)
        return 1;
    void *lib = dlopen("libxcb.so.1", RTLD_NOW | RTLD_GLOBAL);
    if (!lib)
        return 0;
#define R(name) *(void **)&p_##name = dlsym(lib, #name)
    R(xcb_connect); R(xcb_connection_has_error); R(xcb_get_image); R(xcb_get_image_reply);
    R(xcb_get_image_data); R(xcb_get_image_data_length); R(xcb_get_geometry); R(xcb_get_geometry_reply);
#undef R
    if (!p_xcb_connect || !p_xcb_get_image_reply)
        return 0;
    void *c = p_xcb_connect(NULL, NULL);
    if (!c || p_xcb_connection_has_error(c))
        return 0;
    xcb = c;
    return 1;
}

static void resolve_gl(void) {
    if (!real_bind) *(void **)&real_bind = dlsym(RTLD_NEXT, "glBindTexture");
    if (!real_get_integerv) *(void **)&real_get_integerv = dlsym(RTLD_NEXT, "glGetIntegerv");
    if (!real_tex_image) *(void **)&real_tex_image = dlsym(RTLD_NEXT, "glTexImage2D");
    if (!real_tex_sub_image) *(void **)&real_tex_sub_image = dlsym(RTLD_NEXT, "glTexSubImage2D");
}

static struct image *fake(EGLImageKHR img) {
    struct image *i = (struct image *)img;
    return (i >= images && i < images + MAX_IMAGES && i->used) ? i : NULL;
}

// copy the pixmap into the currently bound texture
static void refresh(struct image *im) {
    if (!xcb_ready() || !real_tex_image)
        return;
    xcb_get_geometry_reply_t *g = p_xcb_get_geometry_reply(xcb, p_xcb_get_geometry(xcb, im->pixmap), NULL);
    if (!g)
        return;  // pixmap gone
    int w = g->width, h = g->height;
    free(g);
    // format 2 = ZPixmap, all planes
    xcb_get_image_reply_t *r = p_xcb_get_image_reply(
        xcb, p_xcb_get_image(xcb, 2, im->pixmap, 0, 0, w, h, 0xffffffff), NULL);
    if (!r)
        return;
    uint8_t *px = p_xcb_get_image_data(r);
    int len = p_xcb_get_image_data_length(r);
    if (len >= w * h * 4) {
        int opaque = r->depth != 32;
        for (int k = 0; k < w * h; k++) {  // X: B,G,R,A/X -> GL: R,G,B,A
            uint8_t *p = px + 4 * k, b = p[0];
            p[0] = p[2];
            p[2] = b;
            if (opaque)
                p[3] = 0xff;
        }
        if (!im->uploaded || w != im->width || h != im->height) {
            real_tex_image(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
            im->width = w;
            im->height = h;
            im->uploaded = 1;
            LOG("texture %u <- pixmap 0x%lx %dx%d depth %d\n", im->texture, im->pixmap, w, h, r->depth);
        } else {
            real_tex_sub_image(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px);
        }
    }
    free(r);
    im->last_refresh_ms = now_ms();
}

static EGLImageKHR my_create(EGLDisplay d, EGLContext c, EGLenum target, EGLClientBuffer buf, const EGLint *attrs) {
    // Some native GPU drivers accept the X pixmap as an EGL image but return a
    // black texture when QtCar samples it. Use the proven XCB copy path there.
    EGLImageKHR img = (!force_copy || target != EGL_NATIVE_PIXMAP_KHR) && real_create
        ? real_create(d, c, target, buf, attrs) : NULL;
    if (img || target != EGL_NATIVE_PIXMAP_KHR)
        return img;
    pthread_mutex_lock(&lock);
    for (int k = 0; k < MAX_IMAGES; k++) {
        if (!images[k].used) {
            memset(&images[k], 0, sizeof images[k]);
            images[k].used = 1;
            images[k].pixmap = (unsigned long)buf;
            pthread_mutex_unlock(&lock);
            LOG("stand-in image for pixmap 0x%lx\n", (unsigned long)buf);
            return &images[k];
        }
    }
    pthread_mutex_unlock(&lock);
    return NULL;
}

static EGLBoolean my_destroy(EGLDisplay d, EGLImageKHR img) {
    struct image *im = fake(img);
    if (!im)
        return real_destroy ? real_destroy(d, img) : 0;
    pthread_mutex_lock(&lock);
    im->used = 0;
    pthread_mutex_unlock(&lock);
    return 1;
}

static void my_target(GLenum target, EGLImageKHR img) {
    struct image *im = fake(img);
    if (!im) {
        if (real_target)
            real_target(target, img);
        return;
    }
    resolve_gl();
    GLint tex = 0;
    if (real_get_integerv)
        real_get_integerv(GL_TEXTURE_BINDING_2D, &tex);
    im->texture = (GLuint)tex;
    refresh(im);
}

// interposed: refresh stand-in textures when QtCar draws them
void glBindTexture(GLenum target, GLuint texture) {
    resolve_gl();
    real_bind(target, texture);
    if (target != GL_TEXTURE_2D || !texture)
        return;
    for (int k = 0; k < MAX_IMAGES; k++) {
        struct image *im = &images[k];
        if (im->used && im->texture == texture) {
            if (now_ms() - im->last_refresh_ms >= REFRESH_MS)
                refresh(im);
            break;
        }
    }
}

fnptr eglGetProcAddress(const char *name) {
    static fnptr (*real_gpa)(const char *);
    if (!real_gpa)
        *(void **)&real_gpa = dlsym(RTLD_NEXT, "eglGetProcAddress");
    fnptr real = real_gpa ? real_gpa(name) : NULL;
    if (!name)
        return real;
    if (!strcmp(name, "eglCreateImageKHR")) {
        real_create = (void *)real;
        return (fnptr)my_create;
    }
    if (!strcmp(name, "eglDestroyImageKHR")) {
        real_destroy = (void *)real;
        return (fnptr)my_destroy;
    }
    if (!strcmp(name, "glEGLImageTargetTexture2DOES")) {
        real_target = (void *)real;
        return (fnptr)my_target;
    }
    return real;
}


__attribute__((constructor)) static void init(void) {
    debug = getenv("EGL_MEDIA_PIXMAP_DEBUG") != NULL;
    force_copy = getenv("EGL_MEDIA_PIXMAP_FORCE") != NULL;
}

// QtCar opens the car's center display by name, XOpenDisplay(":0.0"), for its size
// (DisplayInfoUtils::getCenterDisplaySize: the key of games.json's gameWindowSize). Here QtCar's
// screen is its DISPLAY (:1 over VNC): ":0" is the PC's desktop, or can't be opened (then QtCar
// takes 1920x1080), and the arcade's built-in games (2048, chess, ...) got a 16:9 band instead of
// their 1200x1860 window. The car's display name means QtCar's own display.
void *XOpenDisplay(const char *name) {
    static void *(*real_open)(const char *);
    if (!real_open)
        *(void **)&real_open = dlsym(RTLD_NEXT, "XOpenDisplay");
    const char *own = getenv("DISPLAY");
    if (name && own && *own && (!strcmp(name, ":0.0") || !strcmp(name, ":0")))
        name = own;
    return real_open(name);
}
