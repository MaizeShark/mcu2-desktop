/* escalator-stub (mcu2-patchkit, profile 2026.8.3-models): answers QtCar's requests to the car's
 * privileged helper (/usr/bin/escalator, never run here: it can reboot the machine, format USB
 * drives and write GPIOs) from a fixed table, and refuses everything else.
 *
 * Protocol (libescalator / QEscalator): the client connects to the Unix socket /tmp/escalator and
 * sends 0xff, u32 command length, u32 input length (host order), the command ("[detached ]name
 * args...") and the input. Unless detached, it reads u32 exit code, u32 output length, the output.
 *
 * Table: the display checks get "ok" answers (QtCar runs --emulated and shouldn't ask, but
 * they're harmless); start-/stop- of the videos and games go to the chroot's /sbin/sv wrapper
 * (qtcar-service). Every request is logged to stdout (the service's log). */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define SOCKET_PATH "/tmp/escalator"
#define MAX_REQUEST (1 << 20)

static void logf_(const char *fmt, const char *a, const char *b)
{
    char ts[16];
    time_t t = time(NULL);
    strftime(ts, sizeof ts, "%H:%M:%S", localtime(&t));
    printf("%s escalator-stub: ", ts);
    printf(fmt, a, b);
    putchar('\n');
    fflush(stdout);
}

static int read_all(int fd, void *buf, size_t n)
{
    char *p = buf;
    while (n) {
        ssize_t r = read(fd, p, n);
        if (r <= 0) {
            if (r < 0 && errno == EINTR)
                continue;
            return -1;
        }
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

static void write_all(int fd, const void *buf, size_t n)
{
    const char *p = buf;
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w <= 0) {
            if (w < 0 && errno == EINTR)
                continue;
            return;
        }
        p += w;
        n -= (size_t)w;
    }
}

/* sv ACTION SERVICE through the kit's wrapper (runs qtcar-service); its exit code */
static int run_sv(const char *action, const char *service)
{
    pid_t pid = fork();
    if (pid == 0) {
        int null = open("/dev/null", O_RDONLY);
        if (null >= 0)
            dup2(null, 0);
        execl("/sbin/sv", "sv", action, service, (char *)NULL);
        _exit(127);
    }
    int status = 0;
    if (pid < 0 || waitpid(pid, &status, 0) < 0)
        return 1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

/* the jobs QtCar may start and stop: "start-NAME" / "stop-NAME" (and the game launcher's
 * "start-tesla-game-NAME") -> sv start|stop JOB */
static const struct { const char *name, *job; } APPS[] = {
    {"fireplace", "fireplace"},
    {"dog-mode", "dog-mode"},
    {"camp-mode", "camp-mode"},
    {"camp-mode-holiday", "camp-mode-holiday"},
    {"mame", "mame"},
    {"tesla-game-2048", "2048"},
    {"tesla-game-chess", "chess"},
    {"tesla-game-backgammon", "backgammon"},
    /* audiod's media sources: alsaloop from a loopback into AudioWeaver (Chromium's web media,
     * media1 = the media server's USB/streaming player, Bluetooth music); without them it
     * failed to link the source ("Cannot start alsaloop-chromium") and played nothing */
    {"alsaloop-chromium", "alsaloop-chromium"},
    {"media1bridge", "media1bridge"},
    {"a2dpbridge", "a2dpbridge"},
};

/* answered "ok" without doing anything: the panels and the cluster's power, the backlight PWM
 * (the display is the PC's), and jobs ./tesla start runs itself (the media Chromium, valhalla) */
static const char *const QUIET_OK[] = {
    "drm-status-detect", "CLUSTER_POWER", "cluster-power", "pwm-duty-cycle", "pwm-duty-cycle-gen3",
};
static const char *const OK[] = {"start-tesla-chromium-app", "start-valhalla",
    /* the car pins CPUs and pauses services for a game; QtCar exits when this fails */
    "start-game-mode", "stop-game-mode"};

static int listed(const char *word, const char *const *list, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (!strcmp(word, list[i]))
            return 1;
    return 0;
}

/* the answer to one command: exit code, output in out (static) */
static int handle(const char *cmd, const char **out, size_t *out_len)
{
    static char dpcd[0x140];
    char word[128];
    size_t n = strcspn(cmd, " ");
    *out = "";
    *out_len = 0;
    if (n >= sizeof word)
        return 1;
    memcpy(word, cmd, n);
    word[n] = 0;

    if (listed(word, QUIET_OK, sizeof QUIET_OK / sizeof QUIET_OK[0]) ||
        listed(word, OK, sizeof OK / sizeof OK[0]))
        return 0;
    if (!strcmp(word, "read-dpcd")) {
        /* DisplayPortDevice::isTrained reads the lane status digits at 0xa8.. of the text
         * dump; 7 = clock recovery, equalization and symbol lock done on every lane */
        memset(dpcd, '7', sizeof dpcd);
        *out = dpcd;
        *out_len = sizeof dpcd;
        return 0;
    }
    for (size_t i = 0; i < sizeof APPS / sizeof APPS[0]; i++) {
        const char *action = NULL, *name = NULL;
        if (!strncmp(word, "start-", 6))
            action = "start", name = word + 6;
        else if (!strncmp(word, "stop-", 5))
            action = "stop", name = word + 5;
        if (action && !strcmp(name, APPS[i].name)) {
            logf_("%s -> sv %s", word, action);
            return run_sv(action, APPS[i].job);
        }
    }
    return -1;
}

static void serve(int fd)
{
    unsigned char magic;
    uint32_t hdr[2];
    if (read_all(fd, &magic, 1) || magic != 0xff || read_all(fd, hdr, sizeof hdr) ||
        hdr[0] == 0 || hdr[0] > MAX_REQUEST || hdr[1] > MAX_REQUEST) {
        logf_("bad request header%s%s", "", "");
        return;
    }
    char *cmd = calloc(1, hdr[0] + 1);
    char *input = hdr[1] ? malloc(hdr[1]) : NULL;
    if (!cmd || read_all(fd, cmd, hdr[0]) || (input && read_all(fd, input, hdr[1]))) {
        free(cmd);
        free(input);
        return;
    }
    int detached = !strncmp(cmd, "detached ", 9);
    const char *c = detached ? cmd + 9 : cmd;
    const char *out;
    size_t out_len;
    int rc = handle(c, &out, &out_len);
    if (rc < 0) {
        logf_("refused: %s%s", c, "");
        rc = 1;
    } else {
        char word[64];
        size_t n = strcspn(c, " ");
        snprintf(word, sizeof word, "%.*s", (int)(n < sizeof word ? n : sizeof word - 1), c);
        if (!listed(word, QUIET_OK, sizeof QUIET_OK / sizeof QUIET_OK[0]) && strcmp(word, "read-dpcd"))
            logf_("%s -> %s", c, rc ? "failed" : "ok");
    }
    if (!detached) {
        uint32_t reply[2] = {(uint32_t)rc, (uint32_t)out_len};
        write_all(fd, reply, sizeof reply);
        write_all(fd, out, out_len);
    }
    free(cmd);
    free(input);
}

int main(void)
{
    signal(SIGPIPE, SIG_IGN);
    signal(SIGCHLD, SIG_DFL);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    strcpy(a.sun_path, SOCKET_PATH);
    unlink(SOCKET_PATH);
    if (s < 0 || bind(s, (struct sockaddr *)&a, sizeof a) || listen(s, 16)) {
        perror("escalator-stub: " SOCKET_PATH);
        return 1;
    }
    chmod(SOCKET_PATH, 0666);    /* as the car's: QtCar and its services run as other users */
    logf_("listening on %s%s", SOCKET_PATH, "");
    for (;;) {
        int c = accept(s, NULL, NULL);
        if (c < 0)
            continue;
        /* one child per request: starting a game waits for sv; reaped by the double fork */
        pid_t pid = fork();
        if (pid == 0) {
            if (fork() == 0) {
                close(s);
                serve(c);
                _exit(0);
            }
            _exit(0);
        }
        close(c);
        if (pid > 0)
            waitpid(pid, NULL, 0);
    }
}
