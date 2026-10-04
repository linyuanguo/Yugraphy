/* 家谱管理系统 证书管理工具 (Linux, 纯 C 单文件) —— 安装/卸载本中心自签根证书到系统信任库。
 *
 * 与 Windows 版（certtool_dlg.c）配套：把内嵌的 rootCA.crt（EMBEDDED_PEM）装入本机信任库，
 * 使浏览器/系统信任本中心 HTTPS 证书，消除「证书未受系统信任」提示。
 *
 * 用法:
 *   ./cert-tool-linux                 交互菜单（安装/卸载/退出）
 *   ./cert-tool-linux --install       安装
 *   ./cert-tool-linux --uninstall     卸载
 *   ./cert-tool-linux --help          帮助
 *
 * 提权: 写系统信任库需 root。非 root 且为交互式终端且有 sudo 时，自动 sudo 以 root 完整安装；
 *       提权失败或无 TTY 时退化为「用户级安装」（当前用户 Firefox/Chrome NSS 库 + 环境变量说明）。
 *
 * 安装目标:
 *   - 系统信任库：Debian 系 /usr/local/share/ca-certificates + update-ca-certificates；
 *                 RHEL 系 /etc/pki/ca-trust/source/anchors + update-ca-trust；
 *                 Arch  /etc/ca-certificates/trust-source/anchors + update-ca-trust。
 *   - 浏览器（Firefox/Chrome）：各用户 ~/.pki/nssdb，用 certutil (libnss3-tools)。
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include "embedded_pem.h"   /* 内嵌 rootCA.crt(C 字符串常量) */

#define PEM_BASENAME "yugsight_rootCA.crt"
#define NSS_NICK     "家谱管理系统"

static int is_root(void) { return geteuid() == 0; }

static const char *tmpdir(void) {
    const char *t = getenv("TMPDIR");
    return (t && *t) ? t : "/tmp";
}

/* 把内嵌 PEM 写到 dst，0=成功 */
static int write_pem(const char *dst) {
    FILE *f = fopen(dst, "wb");
    if (!f) return -1;
    size_t n = strlen(EMBEDDED_PEM);
    int ok = (fwrite(EMBEDDED_PEM, 1, n, f) == n) && (fflush(f) == 0) && (fclose(f) == 0);
    return ok ? 0 : -1;
}

/* 运行 shell 命令（输出直接到终端），返回退出码；启动失败返回 -1 */
static int run(const char *cmd) {
    fprintf(stderr, "$ %s\n", cmd);
    int rc = system(cmd);
    if (rc == -1) return -1;
    if (WIFEXITED(rc)) return WEXITSTATUS(rc);
    return -1;
}

/* PATH 中是否存在可执行的 name */
static int find_on_path(const char *name) {
    const char *path = getenv("PATH");
    if (!path) return 0;
    char buf[4096];
    strncpy(buf, path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    char *save = NULL;
    for (char *dir = strtok_r(buf, ":", &save); dir; dir = strtok_r(NULL, ":", &save)) {
        char full[4300];
        snprintf(full, sizeof(full), "%s/%s", dir, name);
        if (access(full, X_OK) == 0) return 1;
    }
    return 0;
}

/* ---------- 系统信任库（root） ---------- */
typedef struct { const char *dir; const char *rehash; const char *name; } TrustSys;

static TrustSys detect_trust(void) {
    if (access("/etc/pki/ca-trust/source/anchors", F_OK) == 0)
        return (TrustSys){"/etc/pki/ca-trust/source/anchors", "update-ca-trust extract", "RHEL 系"};
    if (access("/usr/local/share/ca-certificates", F_OK) == 0)
        return (TrustSys){"/usr/local/share/ca-certificates", "update-ca-certificates", "Debian 系"};
    if (access("/etc/ca-certificates/trust-source/anchors", F_OK) == 0)
        return (TrustSys){"/etc/ca-certificates/trust-source/anchors", "update-ca-trust", "Arch"};
    return (TrustSys){"/usr/local/share/ca-certificates", "update-ca-certificates", "Debian 系(默认)"};
}

static void install_system(void) {
    TrustSys ts = detect_trust();
    char dir[512];
    snprintf(dir, sizeof(dir), "%s", ts.dir);
    char mk[600];
    snprintf(mk, sizeof(mk), "mkdir -p '%s' && chmod 0755 '%s'", dir, dir);
    run(mk);
    char file[768];
    snprintf(file, sizeof(file), "%s/%s", dir, PEM_BASENAME);
    if (write_pem(file) != 0) { fprintf(stderr, "✗ 写入 %s 失败\n", file); return; }
    chmod(file, 0644);
    int rc = run(ts.rehash);
    if (rc == 0)
        printf("✅ 系统信任库已更新（%s）：OpenSSL/curl/Java 等将信任本中心证书。\n", ts.name);
    else
        printf("⚠️ 刷新命令「%s」返回 %d，请确认该工具已安装。\n", ts.rehash, rc);
}

static void uninstall_system(void) {
    TrustSys ts = detect_trust();
    char file[768];
    snprintf(file, sizeof(file), "%s/%s", ts.dir, PEM_BASENAME);
    if (access(file, F_OK) == 0) {
        unlink(file);
        printf("已移除 %s\n", file);
    }
    int rc = run(ts.rehash);
    if (rc == 0)
        printf("✅ 系统信任库已刷新（移除 %s）。\n", PEM_BASENAME);
    else
        printf("⚠️ 刷新命令「%s」返回 %d。\n", ts.rehash, rc);
}

/* ---------- 浏览器 NSS 库（Firefox/Chrome） ---------- */
static void install_nss_dir(const char *db) {
    if (access(db, F_OK) != 0) return;      /* 该用户没有 NSS 库，跳过 */
    if (!find_on_path("certutil")) return;
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s/%s.nss", tmpdir(), PEM_BASENAME);
    if (write_pem(tmp) != 0) { fprintf(stderr, "  释放证书失败\n"); return; }
    char cmd[1200];
    snprintf(cmd, sizeof(cmd), "certutil -A -d sql:'%s' -n '%s' -t 'C,,' -i '%s'", db, NSS_NICK, tmp);
    int rc = run(cmd);
    unlink(tmp);
    if (rc == 0) printf("  ✅ 已加入 %s 的 NSS 库\n", db);
    else printf("  ⚠️ %s 的 NSS 库更新返回 %d\n", db, rc);
}

static void uninstall_nss_dir(const char *db) {
    if (access(db, F_OK) != 0) return;
    if (!find_on_path("certutil")) return;
    char cmd[1200];
    snprintf(cmd, sizeof(cmd), "certutil -D -d sql:'%s' -n '%s'", db, NSS_NICK);
    int rc = run(cmd);
    if (rc == 0) printf("  ✅ 已从 %s 的 NSS 库移除\n", db);
    else printf("  (未找到/未移除 %s，返回 %d)\n", db, rc);
}

static void enumerate_nss(void (*fn)(const char *)) {
    const char *home = getenv("HOME");
    if (home) { char d[1024]; snprintf(d, sizeof(d), "%s/.pki/nssdb", home); fn(d); }
    if (is_root()) {
        fn("/root/.pki/nssdb");
        DIR *dir = opendir("/home");
        if (dir) {
            struct dirent *e;
            while ((e = readdir(dir))) {
                if (e->d_name[0] == '.') continue;
                char d[1024];
                snprintf(d, sizeof(d), "/home/%s/.pki/nssdb", e->d_name);
                fn(d);
            }
            closedir(dir);
        }
    }
}

static void install_nss_all(void) {
    if (!find_on_path("certutil")) {
        printf("ℹ️ 未找到 certutil（安装 libnss3-tools 可让 Firefox/Chrome 信任），跳过浏览器库。\n");
        return;
    }
    printf("→ Firefox/Chrome 用户证书库：\n");
    enumerate_nss(install_nss_dir);
}

static void uninstall_nss_all(void) {
    if (!find_on_path("certutil")) return;
    printf("→ Firefox/Chrome 用户证书库：\n");
    enumerate_nss(uninstall_nss_dir);
}

/* ---------- 用户级（无 root 时的兜底） ---------- */
static void install_user(void) {
    const char *home = getenv("HOME");
    if (home) {
        char d[1024], f[1200], mk[1200];
        snprintf(d, sizeof(d), "%s/.local/share/ca-certificates", home);
        snprintf(mk, sizeof(mk), "mkdir -p '%s'", d);
        run(mk);
        snprintf(f, sizeof(f), "%s/%s", d, PEM_BASENAME);
        if (write_pem(f) == 0) printf("✅ 已保存证书到 %s\n", f);
    }
    printf("→ 当前用户 Firefox/Chrome：\n");
    if (home) { char d[1024]; snprintf(d, sizeof(d), "%s/.pki/nssdb", home); install_nss_dir(d); }

    if (home) {
        char f[1200];
        snprintf(f, sizeof(f), "%s/.local/share/ca-certificates/%s", home, PEM_BASENAME);
        printf("\n如需让 curl/OpenSSL/Python/Java 等信任本证书，可设置环境变量：\n");
        printf("  export SSL_CERT_FILE=%s\n", f);
        printf("  export REQUESTS_CA_BUNDLE=%s\n", f);
        printf("  export NODE_EXTRA_CA_CERTS=%s\n", f);
        printf("  export GIT_SSL_CAINFO=%s\n", f);
    }
}

static void uninstall_user(void) {
    const char *home = getenv("HOME");
    if (home) {
        char f[1200];
        snprintf(f, sizeof(f), "%s/.local/share/ca-certificates/%s", home, PEM_BASENAME);
        unlink(f);
        char d[1024];
        snprintf(d, sizeof(d), "%s/.pki/nssdb", home);
        uninstall_nss_dir(d);
    }
}

/* ---------- 提权 ---------- */
static int try_sudo(int action) {
    char self[4096];
    ssize_t n = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (n <= 0) return -1;
    self[n] = 0;
    const char *arg = (action == 0) ? "--install" : "--uninstall";
    pid_t pid = fork();
    if (pid == 0) {
        execlp("sudo", "sudo", "-E", self, arg, (char *)NULL);
        _exit(127);
    }
    if (pid < 0) return -1;
    int st;
    waitpid(pid, &st, 0);
    return (WIFEXITED(st) && WEXITSTATUS(st) == 0) ? 0 : -1;
}

static void usage(void) {
    printf(
        "家谱管理系统证书管理工具 (Linux)\n"
        "  --install     安装证书（写系统信任库 + Firefox/Chrome）\n"
        "  --uninstall   卸载证书\n"
        "  --help        显示本帮助\n"
        "无参数时进入交互菜单。\n");
}

int main(int argc, char **argv) {
    int action = -1;
    if (argc > 1) {
        if (!strcmp(argv[1], "--install")) action = 0;
        else if (!strcmp(argv[1], "--uninstall")) action = 1;
        else if (!strcmp(argv[1], "--help") || !strcmp(argv[1], "-h")) { usage(); return 0; }
        else { usage(); return 2; }
    }

    if (action < 0) {
        if (!isatty(0)) { usage(); return 2; }
        printf("\n家谱管理系统证书管理工具\n  1) 安装证书\n  2) 卸载证书\n  3) 退出\n请选择: ");
        char buf[16];
        if (!fgets(buf, sizeof(buf), stdin)) return 0;
        if (buf[0] == '1') action = 0;
        else if (buf[0] == '2') action = 1;
        else { printf("已退出。\n"); return 0; }
    }

    if (is_root()) {
        if (action == 0) { install_system(); install_nss_all(); }
        else { uninstall_system(); uninstall_nss_all(); }
    } else if (isatty(0) && find_on_path("sudo")) {
        printf("写入系统信任库需要管理员权限，将调用 sudo（可能需要输入密码）。\n");
        if (try_sudo(action) == 0) {
            printf("✅ 已通过 sudo 以 root 完成。\n");
            return 0;
        }
        printf("⚠️ sudo 未成功（已取消或不可用）。改用「用户级」安装（当前用户浏览器 + 环境变量说明）。\n\n");
        if (action == 0) install_user();
        else uninstall_user();
    } else {
        printf("ℹ️ 无 TTY 或无 sudo，执行「用户级」安装（当前用户浏览器 + 环境变量说明）。\n\n");
        if (action == 0) install_user();
        else uninstall_user();
    }

    printf("\n请重启浏览器后重新访问中心端地址（证书未受信任的提示会消失）。\n");
    return 0;
}
