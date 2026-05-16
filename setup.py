# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("LICENSE", "r", encoding="utf-8") as fh:
    license_text = fh.read()

setup(
    name="mingchat-sdk",
    # 版本号统一在 pyproject.toml 中定义
    author="MingChain Tech",
    author_email="contact@mingchain.tech",
    description="铭信 MingChat - BSV区块链上的Agent间通讯协议",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://mingchain.tech",
    project_urls={
        "Documentation": "https://docs.mingchain.tech",
        "Source": "https://github.com/mingchain/mingchat",
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    packages=find_packages(include=["mingchat", "scripts"]),
    python_requires=">=3.9",
    install_requires=[
        "cryptography>=41.0.0",
        "pycryptodome>=3.20.0",
        "requests>=2.31.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "black>=23.0.0",
            "mypy>=1.5.0",
        ],
        "mcp": [
            "mcp>=1.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "mingchat=scripts.cli:main",
        ],
    },
    keywords=["bsv", "blockchain", "messaging", "chat", "agent", "AI", "op_return"],
)
