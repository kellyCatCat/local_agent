"""启动：python -m app"""
import socket

import uvicorn

from .config import settings


def lan_ips() -> list[str]:
    """本机的局域网 IPv4 地址（用于提示其他机器的访问地址）。"""
    ips = set()
    try:
        # 不会真的发包，只是让系统选出默认出口网卡的地址
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


if __name__ == "__main__":
    if settings.host in ("0.0.0.0", "::", ""):
        print(f"Skill 维护 Agent 已监听所有网卡，端口 {settings.port}")
        print(f"  本机访问：http://127.0.0.1:{settings.port}")
        for ip in lan_ips():
            print(f"  局域网访问：http://{ip}:{settings.port}")
        print("  注意：页面没有登录验证，局域网内能访问该地址的人都可以使用和写回 skill 库。")
    else:
        print(f"Skill 维护 Agent: http://{settings.host}:{settings.port}")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
