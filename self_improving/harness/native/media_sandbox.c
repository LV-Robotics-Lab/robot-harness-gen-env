#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/landlock.h>
#include <linux/seccomp.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef LANDLOCK_ACCESS_FS_IOCTL_DEV
#define LANDLOCK_ACCESS_FS_IOCTL_DEV (1ULL << 15)
#endif

#ifndef LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET
#define LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET (1ULL << 0)
#define LANDLOCK_SCOPE_SIGNAL (1ULL << 1)
#endif

#ifndef CLOSE_RANGE_CLOEXEC
#define CLOSE_RANGE_CLOEXEC (1U << 2)
#endif

#ifndef AT_EMPTY_PATH
#define AT_EMPTY_PATH 0x1000
#endif

#define FIXED_EXEC_FD 3
#define FIXED_MEDIA_FD 4
#define MIN_LANDLOCK_ABI 6

struct audited_landlock_ruleset_attr {
    __u64 handled_access_fs;
    __u64 handled_access_net;
    __u64 scoped;
};

extern char **environ;

static void fail(int code, const char *message) {
    size_t length = strlen(message);
    ssize_t ignored = write(STDERR_FILENO, message, length);
    ignored = write(STDERR_FILENO, "\n", 1);
    (void)ignored;
    _exit(code);
}

static int landlock_abi(void) {
    return (int)syscall(SYS_landlock_create_ruleset, NULL, 0,
                        LANDLOCK_CREATE_RULESET_VERSION);
}

static unsigned long long parse_number(const char *text) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        fail(64, "launcher protocol rejected");
    }
    return value;
}

static int parse_fd(const char *text) {
    unsigned long long value = parse_number(text);
    if (value > INT_MAX) {
        fail(64, "launcher protocol rejected");
    }
    return (int)value;
}

static void set_limit(int resource, unsigned long long value) {
    struct rlimit limit = {
        .rlim_cur = (rlim_t)value,
        .rlim_max = (rlim_t)value,
    };
    if (setrlimit(resource, &limit) != 0) {
        fail(74, "resource limit setup failed");
    }
}

static void set_cpu_limit(unsigned long long value) {
    if (value == ULLONG_MAX) {
        fail(64, "launcher protocol rejected");
    }
    struct rlimit limit = {
        .rlim_cur = (rlim_t)value,
        .rlim_max = (rlim_t)(value + 1U),
    };
    if (setrlimit(RLIMIT_CPU, &limit) != 0) {
        fail(74, "resource limit setup failed");
    }
}

static void duplicate_fixed_fds(int executable_fd, int media_fd, int *status_fd) {
    int executable_copy = fcntl(executable_fd, F_DUPFD_CLOEXEC, 20);
    int media_copy = fcntl(media_fd, F_DUPFD_CLOEXEC, 20);
    int status_copy = fcntl(*status_fd, F_DUPFD_CLOEXEC, 20);
    if (executable_copy < 0 || media_copy < 0 || status_copy < 0) {
        fail(76, "descriptor setup failed");
    }
    if (dup3(executable_copy, FIXED_EXEC_FD, O_CLOEXEC) < 0 ||
        dup3(media_copy, FIXED_MEDIA_FD, 0) < 0) {
        fail(76, "descriptor setup failed");
    }
    if (syscall(SYS_close_range, 5U, UINT_MAX, CLOSE_RANGE_CLOEXEC) != 0) {
        fail(76, "descriptor setup failed");
    }
    if (fcntl(FIXED_MEDIA_FD, F_SETFD, 0) != 0) {
        fail(76, "descriptor setup failed");
    }
    *status_fd = status_copy;
}

static void install_landlock(void) {
    int abi = landlock_abi();
    if (abi < MIN_LANDLOCK_ABI) {
        fail(77, "landlock unavailable");
    }
    const __u64 all_fs_access =
        LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE |
        LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR |
        LANDLOCK_ACCESS_FS_REMOVE_DIR | LANDLOCK_ACCESS_FS_REMOVE_FILE |
        LANDLOCK_ACCESS_FS_MAKE_CHAR | LANDLOCK_ACCESS_FS_MAKE_DIR |
        LANDLOCK_ACCESS_FS_MAKE_REG | LANDLOCK_ACCESS_FS_MAKE_SOCK |
        LANDLOCK_ACCESS_FS_MAKE_FIFO | LANDLOCK_ACCESS_FS_MAKE_BLOCK |
        LANDLOCK_ACCESS_FS_MAKE_SYM | LANDLOCK_ACCESS_FS_REFER |
        LANDLOCK_ACCESS_FS_TRUNCATE | LANDLOCK_ACCESS_FS_IOCTL_DEV;
    struct audited_landlock_ruleset_attr ruleset = {
        .handled_access_fs = all_fs_access,
        .handled_access_net = LANDLOCK_ACCESS_NET_BIND_TCP |
                              LANDLOCK_ACCESS_NET_CONNECT_TCP,
        .scoped = LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET | LANDLOCK_SCOPE_SIGNAL,
    };
    int ruleset_fd = (int)syscall(SYS_landlock_create_ruleset, &ruleset,
                                  sizeof(ruleset), 0);
    if (ruleset_fd < 0) {
        fail(77, "landlock setup failed");
    }
    struct landlock_path_beneath_attr executable_rule = {
        .allowed_access = LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE,
        .parent_fd = FIXED_EXEC_FD,
    };
    if (syscall(SYS_landlock_add_rule, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH,
                &executable_rule, 0) != 0 ||
        syscall(SYS_landlock_restrict_self, ruleset_fd, 0) != 0) {
        fail(77, "landlock setup failed");
    }
    (void)close(ruleset_fd);
}

#define DENY_SYSCALL(name)                                                    \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_##name, 0, 1),                   \
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA))

static void install_seccomp(void) {
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x40000000U, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        DENY_SYSCALL(socket),
        DENY_SYSCALL(socketpair),
        DENY_SYSCALL(connect),
        DENY_SYSCALL(bind),
        DENY_SYSCALL(listen),
        DENY_SYSCALL(accept),
        DENY_SYSCALL(accept4),
        DENY_SYSCALL(mount),
        DENY_SYSCALL(umount2),
        DENY_SYSCALL(pivot_root),
        DENY_SYSCALL(ptrace),
        DENY_SYSCALL(unshare),
        DENY_SYSCALL(setns),
        DENY_SYSCALL(bpf),
        DENY_SYSCALL(perf_event_open),
        DENY_SYSCALL(userfaultfd),
        DENY_SYSCALL(open_by_handle_at),
        DENY_SYSCALL(name_to_handle_at),
        DENY_SYSCALL(move_mount),
        DENY_SYSCALL(fsopen),
        DENY_SYSCALL(fsmount),
        DENY_SYSCALL(fspick),
        DENY_SYSCALL(open_tree),
        DENY_SYSCALL(kexec_load),
        DENY_SYSCALL(process_vm_readv),
        DENY_SYSCALL(process_vm_writev),
        DENY_SYSCALL(io_uring_setup),
        DENY_SYSCALL(memfd_create),
        DENY_SYSCALL(keyctl),
        DENY_SYSCALL(add_key),
        DENY_SYSCALL(request_key),
        DENY_SYSCALL(fanotify_init),
        DENY_SYSCALL(inotify_init),
        DENY_SYSCALL(inotify_init1),
        DENY_SYSCALL(open),
        DENY_SYSCALL(openat),
        DENY_SYSCALL(openat2),
        DENY_SYSCALL(creat),
        DENY_SYSCALL(getrandom),
        DENY_SYSCALL(execve),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = {
        .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
        .filter = filter,
    };
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) {
        fail(78, "seccomp setup failed");
    }
}

static void run_sandbox(int argc, char **argv) {
    if (argc < 14 || strcmp(argv[12], "--") != 0) {
        fail(64, "launcher protocol rejected");
    }
    int status_fd = parse_fd(argv[2]);
    int gate_fd = parse_fd(argv[3]);
    int executable_fd = parse_fd(argv[4]);
    int media_fd = parse_fd(argv[5]);
    pid_t parent = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) {
        fail(72, "parent supervision failed");
    }
    if (fcntl(status_fd, F_SETFD, FD_CLOEXEC) != 0 ||
        write(status_fd, "R", 1) != 1) {
        fail(73, "synchronization failed");
    }
    char gate = 0;
    ssize_t received;
    do {
        received = read(gate_fd, &gate, 1);
    } while (received < 0 && errno == EINTR);
    if (received != 1 || gate != 'G') {
        fail(73, "synchronization failed");
    }
    (void)close(gate_fd);
    set_limit(RLIMIT_AS, parse_number(argv[6]));
    set_cpu_limit(parse_number(argv[7]));
    set_limit(RLIMIT_NOFILE, parse_number(argv[8]));
    set_limit(RLIMIT_NPROC, parse_number(argv[9]));
    set_limit(RLIMIT_FSIZE, parse_number(argv[10]));
    set_limit(RLIMIT_CORE, parse_number(argv[11]));
    duplicate_fixed_fds(executable_fd, media_fd, &status_fd);
    if (clearenv() != 0 || setenv("LANG", "C", 1) != 0 ||
        setenv("LC_ALL", "C", 1) != 0 || setenv("TZ", "UTC", 1) != 0) {
        fail(75, "environment setup failed");
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        fail(77, "landlock setup failed");
    }
    install_landlock();
    install_seccomp();
    syscall(SYS_execveat, FIXED_EXEC_FD, "", &argv[13], environ, AT_EMPTY_PATH);
    fail(79, "decoder exec failed");
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--probe-landlock-abi") == 0) {
        int abi = landlock_abi();
        if (abi < 0) {
            return 70;
        }
        if (printf("%d\n", abi) < 0) {
            return 71;
        }
        return 0;
    }
    if (argc >= 2 && strcmp(argv[1], "--run") == 0) {
        run_sandbox(argc, argv);
    }
    fail(64, "launcher protocol rejected");
    return 64;
}
