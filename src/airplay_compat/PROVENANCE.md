These MIT-licensed files are adapted from pyatv commit
8144c77c6cecbed4f9ba2adb5a350ad86a8f6604 in robkochman/pyatv:
https://github.com/robkochman/pyatv/commit/8144c77c6cecbed4f9ba2adb5a350ad86a8f6604

stream.py corresponds to pyatv/protocols/raop/protocols/airplayv2.py.
channels.py corresponds to pyatv/protocols/airplay/channels.py.
The EventChannel import is redirected to this local package.

Pinned dependency: pyatv==0.18.0. This isolates the modern video queue changes
from Chromecast and the globally installed pyatv package. Retain LICENSE.md.
