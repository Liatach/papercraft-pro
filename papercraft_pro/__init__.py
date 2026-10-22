# SPDX-License-Identifier: GPL-3.0-or-later
"""Papercraft Pro: unfold meshes into printable papercraft with glue tabs,
a live page layout beside the model, and a hinge rig that animates the fold."""

from . import editor, live, ops, props, ui

_modules = (props, ops, editor, ui, live)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
