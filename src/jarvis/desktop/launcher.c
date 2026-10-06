/* Jarvis.app's program: it opens the desktop app (Python) and stays running as its parent.

   macOS asks about the mic for the app at the top of a process tree, and remembers the answer
   for that app. Started this way, that's Jarvis.app, however it was opened: a double click,
   Spotlight, or launchd at login. (Replacing this program with Python, as exec() would, makes
   Python the one macOS asks about: a bare program, not an app.)

   `jarvis install` compiles it, filling in PYTHON, PROJECT, SEARCH_PATH and LOG. */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t app = 0;

/* Quitting (launchd at log out, `kill`): pass it on, so Jarvis stops tidily. */
static void pass_on(int signum) {
    if (app > 0) kill(app, signum);
}

int main(void) {
    if (chdir(PROJECT) != 0) return 1;
    setenv("PATH", SEARCH_PATH, 1); /* where livekit-server is: launchd's PATH lacks Homebrew */
    int log = open(LOG, O_WRONLY | O_CREAT | O_TRUNC, 0644); /* stray output, crash reports */
    if (log >= 0) {
        dup2(log, STDOUT_FILENO);
        dup2(log, STDERR_FILENO);
        close(log);
    }
    signal(SIGTERM, pass_on);
    signal(SIGINT, pass_on);
    signal(SIGHUP, pass_on);
    char *args[] = {PYTHON, "-m", "jarvis", "desktop", NULL};
    if (posix_spawn(&app, PYTHON, NULL, NULL, args, environ) != 0) return 1;
    int status;
    while (waitpid(app, &status, 0) < 0) {
        if (errno != EINTR) return 1; /* a signal interrupted the wait: wait on */
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
