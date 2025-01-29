from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="pywssocks",
    version="1.0.0",
    author="jackzzs",
    author_email="jackzzs@outlook.com",
    description="A forward and reverse socks over websocket server and client implementation in Python.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/zetxtech/pywssocks",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "websockets>=13.1",
        "click",
    ],
    entry_points={
        'console_scripts': [
            'pywssocks=pywssocks.cli:cli',
        ],
    },
)
