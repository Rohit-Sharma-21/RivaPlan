# -*- coding: utf-8 -*-

def classFactory(iface):
    from .main import RivaPlanPlugin
    return RivaPlanPlugin(iface)