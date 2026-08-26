"""文件加密检测 + 解密服务兜底。

透明加密（DLP）文件通过浏览器上传时通常已解密（浏览器读文件时 DLP 自动解密），
但通过 scp/共享盘传到服务器时磁盘字节是密文。本模块：
  1. 检测文件是否被加密（magic bytes 判断）。
  2. 若加密，调公司解密服务（http://10.18.87.51:8500/parse_file format=file）还原。

配置（环境变量 / .env）：
  DECRYPT_URL      解密服务地址，默认 http://10.18.87.51:8500/parse_file
  DECRYPT_ENABLED  是否启用解密兜底，默认 true
  DECRYPT_TIMEOUT  超时秒数，默认 30
"""
from __future__ import annotations

import io
import os
import urllib.error
import urllib.request

# 正常 .xlsx 文件是 ZIP 格式，前 4 字节固定 PK\x03\x04
XLSX_MAGIC = b"\x50\x4b\x03\x04"
# OLE2 复合文件（可能是加密容器或旧 .xls）
OLE2_MAGIC = b"\xd0\xcf\x11\xe0"

DECRYPT_URL = os.environ.get("DECRYPT_URL", "http://10.18.87.51:8500/parse_file")
DECRYPT_ENABLED = os.environ.get("DECRYPT_ENABLED", "true").lower() in ("true", "1", "yes")
DECRYPT_TIMEOUT = int(os.environ.get("DECRYPT_TIMEOUT", "30"))


def is_encrypted(data: bytes) -> bool:
    """检测文件字节是否不是正常 xlsx（即可能被加密）。"""
    if len(data) < 4:
        return True
    return data[:4] != XLSX_MAGIC


def decrypt_file(data: bytes, filename: str = "file.xlsx") -> bytes:
    """调解密服务还原加密文件，返回解密后的字节。

    使用 /parse_file 接口，format=file 返回解密后的文件流。
    若解密服务不可用或解密失败，抛 RuntimeError。
    """
    if not DECRYPT_ENABLED:
        raise RuntimeError("解密服务未启用（DECRYPT_ENABLED=false）")

    boundary = b"----CertVerifyDecrypt"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + data + b"\r\n"
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="format"\r\n\r\n'
        b"file\r\n"
        b"--" + boundary + b"--\r\n"
    )

    req = urllib.request.Request(
        DECRYPT_URL,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=DECRYPT_TIMEOUT) as resp:
            result = resp.read()
            if len(result) < 4 or result[:4] != XLSX_MAGIC:
                raise RuntimeError("解密服务返回的内容不是合法 xlsx")
            return result
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"解密服务返回 HTTP {e.code}：{detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"解密服务不可达（{DECRYPT_URL}）：{e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"调用解密服务失败：{type(e).__name__}: {e}") from e
