#define WIN32_LEAN_AND_MEAN
/* 全宽字符: 让 SHELLEXECUTEINFO 等通用名解析成 W 版, 与显式 *W 函数匹配
 * (MinGW 默认解析成 ANSI 版会因结构体布局不同而运行时出错)。 */
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <commctrl.h>
#include <wincrypt.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <string.h>
#include "embedded_pem.h"   /* 内嵌 rootCA.crt(C 字符串常量, 由构建脚本从 rootCA.crt 生成) */

/* MinGW 的 C 运行时没有 MSVC 的 swprintf_s：用 swprintf 等价替换（仅 MinGW 下生效）。
 * 各处 swprintf_s 调用均不检查返回值，swprintf 返回写入字符数，行为兼容。 */
#ifdef __MINGW32__
#ifndef swprintf_s
#define swprintf_s(buf, size, ...) (swprintf((buf), (size), __VA_ARGS__))
#endif
#endif

/*
 * 家谱管理系统 证书管理工具(纯 C 单文件) —— 安装/卸载本中心自签根证书到 Windows Root 存储。
 *
 * 为什么纯 C(而非 Go): 本机 Insider 26200 裁剪构建下, Go 用 syso 注入 comctl32 v6 manifest
 * 不可靠(TaskDialogIndirect 时有时无), 且 CreateWindowExW 直接失败。C 用 .rc 原生嵌
 * comctl32 v6 manifest, 在本机最稳。
 *
 * 提权方式(2026-09-29 最终方案): manifest 声明 requireAdministrator —— 双击启动时
 * Windows 先弹一次 UAC(由系统直接处理, 比 ShellExecute "runas" 稳; runas 在部分构建上
 * 弹不出 UAC), 点「是」后本工具以**管理员身份**运行; 取消 UAC 则不启动。之后**直接**跑
 * certutil(继承管理员 token, 无需再提权), 拿真实退出码。
 *
 * 证书来源: 硬编码为 C 字符串常量 EMBEDDED_PEM(embedded_pem.h), 规避 windres 静默不嵌入
 * RCDATA 的坑(windres 相对工作目录找资源文件, cwd 稍偏就静默丢弃)。
 *
 * 结果提示: 安装/卸载的每一步都有 MessageBoxW 明确提示(成功/失败/启动失败), 解决
 * "装/卸成功与否没提示"的问题。
 */

/* 证书工具标题用系统名「家谱管理系统」(不再用 yugsight)。
 * APPVER 仅写入本地日志 %TEMP%\yugsight_certtool.log 标识构建版本。 */
static const wchar_t *TITLE = L"家谱管理系统证书管理工具";
static const wchar_t *APPVER = L"v6-20261001";

/* 安全拼接(替代 swprintf 的 %s): MinGW 的 swprintf 在 x86_64 下 %s 只读 1 个字符
 * (wine 实测复现, 即「~b NOR」根因), 故字符串一律 catw 逐段拼接; 数字用单参数
 * swprintf(整数参数正常, 已验证)。 */
static void catw(wchar_t *dst, size_t cap, const wchar_t *s)
{
    if (!dst || cap == 0 || !s) return;
    size_t dl = 0;
    while (dl < cap - 1 && dst[dl]) dl++;
    size_t sl = wcslen(s);
    if (dl + sl >= cap) sl = (dl + 1 < cap) ? (cap - dl - 1) : 0;
    for (size_t i = 0; i < sl; i++) dst[dl + i] = s[i];
    dst[dl + sl] = 0;
}
static void itow_w(wchar_t *out, size_t cap, unsigned long v)
{
    swprintf_s(out, cap, L"%lu", v);   /* 单参数 %lu(整数), 正常 */
}

/* 把一行诊断追加到 %TEMP%\yugsight_certtool.log(带时间戳), 便于定位; 失败静默。 */
static void logLine(const wchar_t *s)
{
    wchar_t p[MAX_PATH];
    DWORD n = GetTempPathW(MAX_PATH, p);
    if (n == 0 || n >= MAX_PATH) return;
    swprintf_s(p + n, MAX_PATH - n, L"yugsight_certtool.log");
    HANDLE f = CreateFileW(p, GENERIC_WRITE, FILE_SHARE_READ, NULL, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t line[2400];
    line[0] = 0;
    catw(line, 2400, L"[");
    wchar_t dt[32];
    swprintf_s(dt, 32, L"%04u-%02u-%02u %02u:%02u:%02u",
               (unsigned)st.wYear, (unsigned)st.wMonth, (unsigned)st.wDay,
               (unsigned)st.wHour, (unsigned)st.wMinute, (unsigned)st.wSecond);  /* 6 个整数, 无 %s, 正常 */
    catw(line, 2400, dt);
    catw(line, 2400, L"] ");
    catw(line, 2400, s);
    catw(line, 2400, L"\r\n");
    DWORD wr = 0;
    WriteFile(f, line, (DWORD)(wcslen(line) * sizeof(wchar_t)), &wr, NULL);
    CloseHandle(f);
}

/* ---------- base64 解码(算指纹用; 忽略空白; 遇 '=' 填充即停; 非法字符返回 -1) ---------- */
static int b64decode(const char *in, size_t inlen, BYTE *out, size_t *outlen, size_t outcap)
{
    int vals[256]; int i;
    const char *T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (i=0;i<256;i++) vals[i] = -1;
    for (i=0;i<64;i++) vals[(unsigned char)T[i]] = i;
    size_t o = 0; int bits = 0, acc = 0;
    for (size_t k=0;k<inlen;k++) {
        unsigned char c = in[k];
        if (c=='\n' || c=='\r' || c==' ' || c=='\t') continue;
        if (c=='=') break;                       /* base64 填充, 到此为止 */
        int v = vals[c];
        if (v < 0) return -1;                    /* 非法字符 */
        acc = (acc << 6) | v; bits += 6;
        if (bits >= 8) { bits -= 8; if (o >= outcap) return -1; out[o++] = (acc >> bits) & 0xFF; }
    }
    *outlen = o;
    return 0;
}

/* 计算内嵌证书 SHA1 指纹(大写 40 位 hex = DER 的 SHA1, 即 certutil -delstore 需要的值)。
 * 从 EMBEDDED_PEM 取正文 base64 → DER → SHA1。 */
static int certThumbprint(wchar_t *out, DWORD cap)
{
    if (cap < 41) return -1;
    const char *pem = EMBEDDED_PEM;
    const char *begin = strstr(pem, "-----BEGIN CERTIFICATE-----");
    const char *end   = strstr(pem, "-----END CERTIFICATE-----");
    if (!begin || !end || end <= begin) return -1;
    const char *body = begin + strlen("-----BEGIN CERTIFICATE-----");
    size_t bodylen = (size_t)(end - body);

    size_t dcap = bodylen * 3 / 4 + 8;
    BYTE *der = (BYTE *)malloc(dcap);
    size_t derlen = 0;
    if (!der || b64decode(body, bodylen, der, &derlen, dcap) != 0 || derlen == 0) {
        free(der);
        return -1;
    }

    HCRYPTPROV hProv = 0;
    int rc = -1;
    if (CryptAcquireContextW(&hProv, NULL, NULL, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT)) {
        HCRYPTHASH hHash = 0;
        if (CryptCreateHash(hProv, CALG_SHA1, 0, 0, &hHash) && CryptHashData(hHash, der, (DWORD)derlen, 0)) {
            BYTE hash[20]; DWORD hl = 20;
            if (CryptGetHashParam(hHash, HP_HASHVAL, hash, &hl, 0) && hl == 20) {
                for (DWORD i = 0; i < 20; i++)
                    swprintf_s(out + i * 2, 3, L"%02X", (unsigned)hash[i]);
                out[40] = 0;
                rc = 0;
            }
        }
        if (hHash) CryptDestroyHash(hHash);
        CryptReleaseContext(hProv, 0);
    }
    free(der);
    return rc;
}

/* 把内嵌证书(EMBEDDED_PEM)写到指定文件, 0=成功。 */
static int extractCert(const wchar_t *dst)
{
    const char *pem = EMBEDDED_PEM;
    DWORD sz = (DWORD)strlen(pem);
    HANDLE f = CreateFileW(dst, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    int rc = -1;
    if (f != INVALID_HANDLE_VALUE) {
        DWORD wr = 0;
        BOOL ok = WriteFile(f, pem, sz, &wr, NULL);
        CloseHandle(f);
        rc = (ok && wr == sz) ? 0 : -1;
    }
    return rc;
}

/* 直接运行 certutil(本工具已以 requireAdministrator 启动, 有管理员权限, 无需 runas),
 * 隐藏其控制台窗口, 等其退出并返回退出码。
 * 返回 0=成功运行(退出码写入 *code); -1=启动失败(诊断写入 *err, *code 置 -1)。 */
static int runCertutil(const wchar_t *params, int *code, wchar_t *err, DWORD errcap)
{
    /* 系统目录用独立缓冲区(避免 swprintf 源=目标重叠), 末尾反斜杠去掉避免双反斜杠 */
    wchar_t sysdir[MAX_PATH];
    DWORD dn = GetSystemDirectoryW(sysdir, MAX_PATH);
    if (dn == 0 || dn >= MAX_PATH) {
        *code = -1;
        swprintf_s(err, errcap, L"获取系统目录失败(错误码 %lu), 请重试。",
                   (unsigned long)GetLastError());
        logLine(err);
        return -1;
    }
    while (dn > 1 && (sysdir[dn - 1] == L'\\' || sysdir[dn - 1] == L'/')) sysdir[--dn] = 0;

    wchar_t cu[MAX_PATH];
    wcscpy(cu, sysdir);
    catw(cu, MAX_PATH, L"\\certutil.exe");

    /* 先验证 certutil.exe 确实存在: 区分「路径/文件问题」与「命令行参数问题」, 便于定位 */
    if (GetFileAttributesW(cu) == INVALID_FILE_ATTRIBUTES) {
        *code = -1;
        err[0] = 0;
        catw(err, errcap, L"找不到 certutil.exe: ");
        catw(err, errcap, cu);
        catw(err, errcap, L" (错误码 ");
        wchar_t num[32];
        itow_w(num, 32, (unsigned long)GetLastError());
        catw(err, errcap, num);
        catw(err, errcap, L")");
        logLine(err);
        return -1;
    }

    /* 完整命令行(含程序路径, 供 certutil 解析; argv[0] 必须是程序名, 故把 cu 拼进去) */
    wchar_t cmd[2048];
    cmd[0] = 0;
    catw(cmd, 2048, L"\"");
    catw(cmd, 2048, cu);
    catw(cmd, 2048, L"\" ");
    catw(cmd, 2048, params);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);   /* MinGW 的 STARTUPINFOW 字段名是 cb(非 cbSize) */
    si.wShowWindow = SW_HIDE;
    ZeroMemory(&pi, sizeof(pi));

    /* 与参考实现一致: lpApplicationName=NULL, 仅靠命令行首 token 定位程序。
     * v1 报「错误 2」的真正根因是 certutilPath 拼出双反斜杠导致首 token 路径无效,
     * 上面已用独立缓冲 + 去末尾反斜杠把路径修正为 C:\Windows\System32\certutil.exe。
     * 路径干净后, 此调用方式(参考版本验证可用)最稳。 */
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, NULL, &si, &pi)) {
        *code = -1;
        err[0] = 0;
        catw(err, errcap, L"启动 certutil 失败(错误码 ");
        wchar_t num[32];
        itow_w(num, 32, (unsigned long)GetLastError());
        catw(err, errcap, num);
        catw(err, errcap, L")。\n程序: ");
        catw(err, errcap, cu);
        catw(err, errcap, L"\n命令: ");
        catw(err, errcap, cmd);
        logLine(err);
        return -1;
    }
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, 120000);
    DWORD c = 0;
    GetExitCodeProcess(pi.hProcess, &c);
    CloseHandle(pi.hProcess);
    {
        wchar_t l[256];
        swprintf_s(l, 256, L"certutil 运行完毕, 退出码=%lu (0=成功)", (unsigned long)c);
        logLine(l);
    }
    *code = (int)c;
    return 0;
}

/* 弹三按钮 TaskDialog(安装/卸载/退出), 返回 0=安装 1=卸载 2=退出; TaskDialog 不可用时
 * 回退 MessageBoxW(是=安装 否=卸载 取消=退出)。 */
static int showChoice(void)
{
    TASKDIALOGCONFIG cfg;
    ZeroMemory(&cfg, sizeof(cfg));
    cfg.cbSize = sizeof(cfg);
    cfg.dwFlags = TDF_ALLOW_DIALOG_CANCELLATION | TDF_SIZE_TO_CONTENT;
    cfg.pszWindowTitle = TITLE;
    cfg.pszMainInstruction = L"请选择操作(完成后请重启浏览器并打开中心端地址)";
    cfg.pszContent = L"点「安装证书」或「卸载证书」执行操作; 点「退出」= 直接关闭本工具。";

    static const TASKDIALOG_BUTTON buttons[3] = {
        { 1001, L"安装证书" },
        { 1002, L"卸载证书" },
        { 1003, L"退出" },
    };
    cfg.pButtons = buttons;
    cfg.cButtons = 3;
    cfg.nDefaultButton = 1001;

    int clicked = 0;
    HRESULT hr = TaskDialogIndirect(&cfg, &clicked, NULL, NULL);
    if (FAILED(hr)) {
        int r = MessageBoxW(NULL,
            L"请选择操作(完成后请重启浏览器并打开中心端地址)。\n点「是」= 安装证书, 点「否」= 卸载证书, 点「取消」= 退出。",
            TITLE, MB_YESNOCANCEL | MB_ICONINFORMATION);
        if (r == IDYES) return 0;
        if (r == IDNO)  return 1;
        return 2;
    }
    if (clicked == 1001) return 0;
    if (clicked == 1002) return 1;
    return 2; /* 退出按钮, 或 ×/ESC 取消, 均视为退出 */
}

/* 执行安装, 把结果文案写入 msg。
 * 双存储安装(浏览器校验时两个存储都会查):
 *   1) 系统存储 LocalMachine\Root: 本工具已提权, 直接 -addstore;
 *   2) 当前用户存储 CurrentUser\Root: -user -addstore。浏览器实际信任的根证书常
 *      落在此存储(受限环境机器存储写不进去时, 只能 -user 装到这里)。
 * 旧版只操作机器存储 → 用户存储里装不上/清不掉, 造成"装了不生效/卸了仍显示安全"。 */
static void doInstall(wchar_t *msg, DWORD cap)
{
    /* 内嵌证书释放到系统临时目录(非 exe 目录, 避免只读位置写失败) */
    wchar_t tmp[MAX_PATH];
    DWORD n = GetTempPathW(MAX_PATH, tmp);
    swprintf_s(tmp + n, MAX_PATH - n, L"yugsight_rootCA_%lu_%lu.crt",
               (unsigned long)GetCurrentProcessId(), (unsigned long)GetTickCount());
    if (extractCert(tmp) != 0) {
        swprintf_s(msg, cap, L"释放内嵌证书失败, 请重试。");
        return;
    }

    int codeM = -1, codeU = -1, launchFail = 0;
    wchar_t diag[1024] = L"";
    wchar_t params[1200];

    /* 1) 系统存储 LocalMachine\Root(本工具已提权) */
    params[0] = 0;
    catw(params, 1200, L"-addstore \"Root\" \"");
    catw(params, 1200, tmp);
    catw(params, 1200, L"\"");
    if (runCertutil(params, &codeM, diag, 1024) != 0) { codeM = -1; launchFail = 1; }

    /* 2) 当前用户存储 CurrentUser\Root(浏览器常信任此存储) */
    params[0] = 0;
    catw(params, 1200, L"-user -addstore \"Root\" \"");
    catw(params, 1200, tmp);
    catw(params, 1200, L"\"");
    if (runCertutil(params, &codeU, diag, 1024) != 0) { codeU = -1; launchFail = 1; }

    DeleteFileW(tmp);

    if (launchFail) {
        /* certutil 启动本身失败: 展示诊断(含程序路径/命令/错误码), 不再报返回码 */
        msg[0] = 0;
        catw(msg, cap, diag);
        return;
    }
    if (codeM == 0 && codeU == 0) {
        swprintf_s(msg, cap, L"证书安装完成(系统存储 + 当前用户存储)。\n\n请完全关闭浏览器(关所有窗口, 任务管理器确认无残留进程)后重新打开中心端地址, 提示才会消失。");
    } else if (codeU == 0) {
        swprintf_s(msg, cap, L"当前用户证书安装成功, 你的浏览器已可信任。\n系统存储安装失败(certutil 返回码 %d), 本机其他用户需另行安装。", codeM);
    } else if (codeM == 0) {
        swprintf_s(msg, cap, L"系统存储安装成功。\n当前用户存储安装失败(certutil 返回码 %d), 你的浏览器暂不受信任。", codeU);
    } else {
        swprintf_s(msg, cap, L"证书安装失败(系统存储 %d / 当前用户存储 %d)。\n\n请重试。", codeM, codeU);
    }
}

/* 执行卸载, 把结果文案写入 msg。
 * 与安装对称, 两个存储都清: 系统存储 + 当前用户存储。
 * 注意: certutil -delstore 对"存储中本就没有该证书"也返回 0(命令成功完成),
 * 故这里"成功"= 两个存储都已尝试清除, 是否原本装有由前面安装结果决定。 */
static void doUninstall(wchar_t *msg, DWORD cap)
{
    wchar_t thumb[64] = {0};
    if (certThumbprint(thumb, 64) != 0) {
        swprintf_s(msg, cap, L"计算证书指纹失败, 无法卸载。");
        return;
    }

    int codeM = -1, codeU = -1, launchFail = 0;
    wchar_t diag[1024] = L"";
    wchar_t params[1200];

    /* 1) 系统存储 */
    params[0] = 0;
    catw(params, 1200, L"-delstore \"Root\" ");
    catw(params, 1200, thumb);
    if (runCertutil(params, &codeM, diag, 1024) != 0) { codeM = -1; launchFail = 1; }

    /* 2) 当前用户存储 */
    params[0] = 0;
    catw(params, 1200, L"-user -delstore \"Root\" ");
    catw(params, 1200, thumb);
    if (runCertutil(params, &codeU, diag, 1024) != 0) { codeU = -1; launchFail = 1; }

    if (launchFail) {
        /* certutil 启动本身失败: 展示诊断 */
        msg[0] = 0;
        catw(msg, cap, diag);
        return;
    }
    if (codeM == 0 && codeU == 0) {
        swprintf_s(msg, cap, L"证书已从系统存储与当前用户存储清除。\n\n请完全重启浏览器(退出所有窗口)后再访问, 连接将变为未受信任。");
    } else {
        swprintf_s(msg, cap, L"证书卸载未完全: 系统存储返回码 %d, 当前用户存储返回码 %d。\n请重试, 或手动检查 certmgr.msc 的「受信任的根证书颁发机构」。", codeM, codeU);
    }
}

/* MinGW 的 -mwindows CRT 固定调用 WinMain(不受 UNICODE 宏影响), 故入口名必须叫 WinMain。 */
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmd, int nShow)
{
    (void)hInst; (void)hPrev; (void)lpCmd; (void)nShow;

    /* 本工具由 manifest 声明 requireAdministrator: 双击启动时 Windows 已弹 UAC 并以
     * 管理员身份运行(取消 UAC 则不启动, 无后续动作)。故此处直接跑 certutil 即有权限。 */
    INITCOMMONCONTROLSEX icc;
    icc.dwSize = sizeof(icc);
    icc.dwICC = ICC_WIN95_CLASSES;
    InitCommonControlsEx(&icc);

    /* 启动即记录版本与系统目录: 弹窗标题带 v3 可确认版本, 日志留完整诊断 */
    {
        wchar_t sd[MAX_PATH];
        DWORD sdn = GetSystemDirectoryW(sd, MAX_PATH);
        wchar_t l[512];
        l[0] = 0;
        catw(l, 512, L"启动 ");
        catw(l, 512, APPVER);
        catw(l, 512, L" | GetSystemDirectoryW=");
        catw(l, 512, sdn ? sd : L"(失败)");
        catw(l, 512, L" (len=");
        wchar_t nlen[32];
        itow_w(nlen, 32, (unsigned long)sdn);
        catw(l, 512, nlen);
        catw(l, 512, L")");
        logLine(l);
    }

    for (;;) {
        int choice = showChoice();
        if (choice == 2) break;            /* 退出 */

        wchar_t msg[2048] = L"";
        if (choice == 0) doInstall(msg, 2048);
        else              doUninstall(msg, 2048);

        logLine(msg);   /* 记录到日志便于排查 */

        MessageBoxW(NULL, msg, TITLE, MB_OK | MB_ICONINFORMATION);
    }
    return 0;
}
