#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成内网 HTTPS 自签证书（根 CA + 服务器证书），输出到 certs/。

为什么用 cryptography 而不是 openssl req -subj：
  openssl 的 -subj 对中文会按平台字符集二次编码，CN 会变成 çç¯… 乱码（双重编码）。
  cryptography 直接把 Python str 按 UTF8String 精确写入，避免该坑。

关键约定（历史坑，务必遵守）：
  1) 分发给客户端的 rootCA.crt 必须是「签发 server.crt 的这张 CA」本身，
     二者必须是同一张证书，否则客户端链校验失败 → 装完仍显示「不安全」。
  2) CA 名称用系统名「家谱管理系统」（不再是 yugsight / Genealogy-Internal-CA）。
  3) server.crt 的 SAN 必须含访问 IP，浏览器按 IP 访问时才能匹配。
"""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(os.path.dirname(HERE), "certs")
os.makedirs(DIR, exist_ok=True)

CA_NAME = "家谱管理系统"
SERVER_IP = "172.16.199.16"
DAYS = 3650
now = datetime.datetime.utcnow()
not_before = now - datetime.timedelta(days=1)
not_after = now + datetime.timedelta(days=DAYS)

# ---------- 根 CA（自签，CA:TRUE） ----------
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(not_before)
    .not_valid_after(not_after)
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False),
        critical=True)
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
    .sign(ca_key, hashes.SHA256())
)

# ---------- 服务器证书（由该 CA 签发，SAN=访问 IP） ----------
srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
srv_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_IP)])
srv_cert = (
    x509.CertificateBuilder()
    .subject_name(srv_name)
    .issuer_name(ca_name)
    .public_key(srv_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(not_before)
    .not_valid_after(not_after)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False),
        critical=True)
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(SERVER_IP))]), critical=False)
    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    .sign(ca_key, hashes.SHA256())
)


def w(path, data, mode):
    p = os.path.join(DIR, path)
    with open(p, "wb") as f:
        f.write(data)
    os.chmod(p, mode)


w("ca.key", ca_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()), 0o600)
w("ca.crt", ca_cert.public_bytes(serialization.Encoding.PEM), 0o644)
w("server.key", srv_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()), 0o600)
w("server.crt", srv_cert.public_bytes(serialization.Encoding.PEM), 0o644)

# ---------- 校验并打印 ----------
ca_cn = ca_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
srv_cn = srv_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
try:
    san = srv_cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_ip_addresses()
except Exception:
    san = [ipaddress.ip_address(SERVER_IP)]
fp = ca_cert.fingerprint(hashes.SHA1()).hex().upper()
print("CA CN        :", ca_cn, "| 是否期望:", ca_cn == CA_NAME)
print("Server CN    :", srv_cn)
print("Server SAN   :", san)
print("CA SHA1 指纹 :", ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)))
print("✓ 输出目录:", DIR)
