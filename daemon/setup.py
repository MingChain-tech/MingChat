"""
P2P Chat — setup.py
安装: pip install .
开发: pip install -e .
打包: python setup.py sdist
"""
from setuptools import setup, find_packages

setup(
    name="p2pchat",
    version="1.0.0",
    description="去中心化 P2P 加密即时通讯",
    long_description=open("README_USER.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="P2P Chat Team",
    python_requires=">=3.9",
    install_requires=[
        "cryptography>=3.0",
        "requests>=2.25",
        "bsv",
    ],
    py_modules=[
        "crypto", "identity", "transport", "message",
        "spv", "onchain", "chain", "app", "cli"
    ],
    entry_points={
        "console_scripts": [
            "p2pchat=cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Communications :: Chat",
    ],
)
