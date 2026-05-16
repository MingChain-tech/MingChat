#!/usr/bin/env python3
"""
铭信 Bridge CLI — 管理守护进程的生命周期

用法:
  python3 bridge_cli.py start         启动
  python3 bridge_cli.py stop          停止
  python3 bridge_cli.py restart       重启
  python3 bridge_cli.py status        查看状态
  python3 bridge_cli.py logs [N]      查看最后N行日志
  python3 bridge_cli.py health        快速健康检查
"""
import sys, os, time, json, subprocess
from pathlib import Path

# ── 配置 ──
BRIDGE_DIR = Path(__file__).parent
BRIDGE_SCRIPT = BRIDGE_DIR / "bridge_server.py"
DATA_DIR = Path.home() / ".mingchat" / "bridge"
PID_FILE = DATA_DIR / "bridge.pid"
LOG_FILE = DATA_DIR / "bridge.log"


def _read_pid() -> int:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except Exception:
            return 0
    return 0


def _write_pid(pid: int):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _find_process() -> int:
    """找到bridge_server.py的进程ID"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bridge_server.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(p) for p in result.stdout.strip().split()]
            # 排除自己
            my_pid = os.getpid()
            pids = [p for p in pids if p != my_pid]
            return pids[0] if pids else 0
    except Exception:
        pass
    return _read_pid()


def cmd_start():
    pid = _find_process()
    if pid and _is_running(pid):
        print(f"Bridge 已在运行 (PID={pid})")
        return

    env = os.environ.copy()
    nohup_out = DATA_DIR / "nohup.out"
    with open(nohup_out, "a") as f:
        proc = subprocess.Popen(
            [sys.executable, str(BRIDGE_SCRIPT)],
            stdout=f, stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    _write_pid(proc.pid)
    print(f"Bridge 已启动 (PID={proc.pid})")
    print(f"日志: {LOG_FILE}")


def cmd_stop():
    pid = _find_process()
    if not pid or not _is_running(pid):
        print("Bridge 未运行")
        _remove_pid()
        return

    print(f"正在停止 Bridge (PID={pid})...")
    os.kill(pid, 15)  # SIGTERM
    for i in range(10):
        if not _is_running(pid):
            print(f"已停止")
            _remove_pid()
            return
        time.sleep(0.5)
    # 强制终止
    try:
        os.kill(pid, 9)
        print(f"已强制终止 (SIGKILL)")
    except Exception:
        pass
    _remove_pid()


def cmd_restart():
    cmd_stop()
    time.sleep(1)
    cmd_start()


def cmd_status():
    pid = _find_process()
    running = pid and _is_running(pid)

    print(f"Bridge 状态:")
    print(f"  运行: {'✅ 是' if running else '❌ 否'}")
    if running:
        print(f"  PID: {pid}")
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
            # 找最后几条关键日志
            for line in lines[-5:]:
                print(f"  📋 {line.strip()}")
        except Exception:
            pass

    # 尝试HTTP健康检查
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8900/health", timeout=3)
        data = json.loads(resp.read())
        print(f"  HTTP: ✅ {data}")
    except Exception:
        print(f"  HTTP: ❌ 端口8900不可达")

    print(f"  数据目录: {DATA_DIR}")
    print(f"  日志: {LOG_FILE}")


def cmd_logs(n: int = 50):
    if not LOG_FILE.exists():
        print(f"日志文件不存在: {LOG_FILE}")
        return
    with open(LOG_FILE) as f:
        lines = f.readlines()
    tail = lines[-n:] if n < len(lines) else lines
    for line in tail:
        print(line.rstrip())


def cmd_health():
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8900/health", timeout=3)
        data = json.loads(resp.read())
        print(json.dumps(data, ensure_ascii=False, indent=2))
        if data.get("status") == "ok":
            print("\n✅ Bridge 运行正常")
        else:
            print("\n⚠️ Bridge 状态异常")
    except Exception as e:
        print(f"❌ Bridge 不可达: {e}")


def main():
    if len(sys.argv) < 2:
        print("用法: bridge_cli.py <start|stop|restart|status|logs|health> [N]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "status":
        cmd_status()
    elif cmd == "logs":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        cmd_logs(n)
    elif cmd == "health":
        cmd_health()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
