#!/usr/bin/env python
import sys, re, optparse, os
from ctypes import *

"""
This script will use the prototypes from "checkdocs.py -s" to concoct
a 1:1 Python wrapper for Allegro.
"""

class _AL_UTF8String: pass

class Allegro:
    def __init__(self):
        self.types = {}
        self.functions = {}
        self.constants = {}

    def add_struct(self, name):
        x = type(name, (Structure, ), {})
        self.types[name] = x

    def add_union(self, name):
        x = type(name, (Union, ), {})
        self.types[name] = x

    def get_type(self, ptype):
        conversion = {
            "bool" : c_bool,
            "_Bool" : c_bool,
            "char" : c_byte,
            "unsignedchar" : c_ubyte,
            "int" : c_int,
            "unsigned" : c_uint,
            "unsignedint" : c_uint,
            "int16_t" : c_int16,
            "int32_t" : c_int32,
            "uint32_t" : c_uint32,
            "int64_t" : c_int64,
            "uint64_t" : c_uint64,
            "uintptr_t" : c_void_p,
            "intptr_t" : c_void_p,
            "GLuint" : c_uint,
            "unsignedlong" : c_ulong,
            "long" : c_long,
            "size_t" : c_size_t,
            "off_t" : c_int64,
            "time_t" : c_int64,
            "va_list" : c_void_p,
            "float" : c_float,
            "double" : c_double,
            "al_fixed" : c_int,
            "HWND" : c_void_p,
            "char*" : _AL_UTF8String,
            
            # hack: this probably shouldn't be in the public docs
            "postprocess_callback_t" : c_void_p,
            }
        
        ptype = re.sub(r"\bconst\b", "", ptype)
        ptype = re.sub(r"\extern\b", "", ptype)
        ptype = re.sub(r"\__inline__\b", "", ptype)
        ptype = re.sub(r"\s+", "", ptype)

        if ptype.endswith("*"):
            if ptype in conversion:
                return conversion[ptype]
            t = ptype[:-1]
            if t in self.types:
                return POINTER(self.types[t])
            return c_void_p
        elif ptype in self.types:
            return self.types[ptype]
        else:
            try:
                return conversion[ptype]
            except KeyError:
                print("Error:" + str(ptype))
        return None

    def parse_funcs(self, funcs):
        """
        Go through all documented functions and add their prototypes
        as Python functions.
        
        The file should have been generated by Allegro's documentation
        generation scripts.
        """

        for func in funcs:
            name, proto = func.split(":", 1)
            if not name.startswith("al_"): continue
            proto = proto.strip()
            name = name[:-2]
            if proto.startswith("enum"): continue
            if proto.startswith("typedef"): continue
            if "=" in proto: continue
            if proto.startswith("#"): continue
            funcstart = proto.find(name)
            funcend = funcstart + len(name)
            ret = proto[:funcstart].rstrip()
            params = proto[funcend:].strip(" ;")
            if params[0] != "(" or params[-1] != ")":
                print("Error:")
                print(params)
                continue
            params2 = params[1:-1]
            # remove callback argument lists
            balance = 0
            params = ""
            for c in params2:
                if c == ")": balance -= 1
                if balance == 0:
                    params += c
                if c == "(": balance += 1
            params = params.split(",")
            plist = []
            for param in params:
                param = re.sub(r"\bconst\b", "", param)
                param = param.strip()
                if param == "void": continue
                if param == "": continue
                if param == "...": continue

                # treat arrays as a void pointer, for now
                if param.endswith("]") or param.endswith("*"):
                    plist.append(c_void_p)
                    continue
                
                # treat callbacks as a void pointer, for now
                if param.endswith(")"):
                    plist.append(c_void_p)
                    continue

                mob = re.match("^.*?(\w+)$", param)
                if mob:
                    pnamepos = mob.start(1)
                    if pnamepos == 0:
                       # Seems the parameter is not named
                       pnamepos = len(param)
                else:
                    print(params)
                    print(proto)
                    print("")
                    continue
                ptype = param[:pnamepos]
                ptype = self.get_type(ptype)
                plist.append(ptype)

            f = type("", (object, ), {"restype": c_int})
            if not ret.endswith("void"):
                f.restype = self.get_type(ret)
            try:
                f.argtypes = plist
            except TypeError, e:
                print(e)
                print(name)
                print(plist)
            self.functions[name] = f

    def parse_protos(self, filename):
        protos = []
        unions = []
        funcs = []

        # first pass: create all structs, but without fields
        for line in open(filename):
            name, proto = line.split(":", 1)
            proto = proto.lstrip()
            if name.endswith("()"):
                funcs.append(line)
                continue
            # anonymous structs have no name at all
            if name and not name.startswith("ALLEGRO_"): continue
            if name == "ALLEGRO_OGL_EXT_API": continue
            if proto.startswith("union") or\
                proto.startswith("typedef union"):
                self.add_union(name)
                unions.append((name, proto))
            elif proto.startswith("struct") or\
                proto.startswith("typedef struct"):
                self.add_struct(name)
                protos.append((name, proto))
            elif proto.startswith("enum") or\
                proto.startswith("typedef enum"):
                if name: self.types[name] = c_int
                protos.append(("", proto))
            elif proto.startswith("#define"):
                if not name.startswith("_") and not name.startswith("GL_"):
                    i = eval(proto.split(None, 2)[2])
                    self.constants[name] = i
            else:
                # actual typedef
                mob = re.match("typedef (.*) " + name, proto)
                if mob:
                    t = mob.group(1)
                    self.types[name] = self.get_type(t.strip())
                else:
                    # Probably a function pointer
                    self.types[name] = c_void_p

        # Unions must come last because they finalize the fields.
        protos += unions

        # second pass: fill in fields
        for name, proto in protos:
            bo = proto.find("{")
            if bo == -1:
                continue
            bc = proto.rfind("}")
            braces = proto[bo + 1:bc]
           
            if proto.startswith("enum") or \
                proto.startswith("typedef enum"):
                
                fields = braces.split(",")
                i = 0
                for field in fields:
                    if "=" in field:
                        fname, val = field.split("=", 1)
                        fname = fname.strip()
                        i = int(eval(val, globals(), self.constants))
                    else:
                        fname = field.strip()
                    if not fname: continue
                    self.constants[fname] = i
                    i += 1
                continue

            balance = 0
            fields = [""]
            for c in braces:
                if c == "{": balance += 1
                if c == "}": balance -= 1
                if c == ";" and balance == 0:
                    fields.append("")
                else:
                    fields[-1] += c

            flist = []
            for field in fields:
                if not field: continue
                
                # add function pointer as void pointer
                mob = re.match(".*?\(\*(\w+)\)", field)
                if mob:
                    flist.append((mob.group(1), "c_void_p"))
                    continue
                
                # add any pointer as void pointer
                mob = re.match(".*?\*(\w+)$", field)
                if mob:
                    flist.append((mob.group(1), "c_void_p"))
                    continue
                
                # add an array
                mob = re.match("(.*)( \w+)\[(.*?)\]$", field)
                if mob:
                    # this is all a hack
                    n = 0
                    ftype = mob.group(1)
                    if ftype.startswith("struct"):
                        if ftype == "struct {float axis[3];}":
                           t = "c_float * 3"
                        else:
                           print("Error: Can't parse " + ftype + " yet.")
                           t = None
                    else:
                        n = mob.group(3)
                        # something in A5 uses a 2d array
                        if "][" in n: n = n.replace("][", " * ")
                        # something uses a division expression
                        if "/" in n:
                           n = "(" + n.replace("/", "//") + ")"
                        t = self.get_type(ftype).__name__ + " * " + n
                    fname = mob.group(2)
                    flist.append((fname, t))
                    continue
                
                vars = field.split(",")
                mob = re.match("\s*(.*?)\s+(\w+)\s*$", vars[0])
                t = self.get_type(mob.group(1))
                flist.append((mob.group(2), t.__name__))
                for v in vars[1:]:
                    flist.append((v.strip(), t.__name__))

            try: self.types[name].my_fields = flist
            except AttributeError:
                print(name, flist)
                
        self.parse_funcs(funcs)

def main():
    p = optparse.OptionParser()
    p.add_option("-o", "--output", help = "location of generated file")
    p.add_option("-p", "--protos", help = "A file with all " +
        "prototypes to generate Python wrappers for, one per line. "
        "Generate it with docs/scripts/checkdocs.py -p")
    p.add_option("-t", "--type", help = "the library type to " +
        "use, e.g. debug")
    p.add_option("-v", "--version", help = "the library version to " +
        "use, e.g. 5.1")
    options, args = p.parse_args()
    
    if not options.protos:
        p.print_help()
        return

    al = Allegro()

    al.parse_protos(options.protos)

    f = open(options.output, "w") if options.output else sys.stdout

    release = options.type
    version = options.version
    f.write(r"""# Generated by generate_python_ctypes.py.
import os, platform, sys
from ctypes import *
from ctypes.util import *

# You must adjust this function to point ctypes to the A5 DLLs you are
# distributing.
_dlls = []
def _add_dll(name):
    release = "%(release)s"
    if os.name == "nt":
        release = "%(release)s-$(version)s"
    
    # Under Windows, DLLs are found in the current directory, so this
    # would be an easy way to keep all your DLLs in a sub-folder.
    
    # os.chdir("dlls")

    path = find_library(name + release)
    if not path:
        if os.name == "mac":
            path = name + release + ".dylib"
        elif os.name == "nt":
            path = name + release + ".dll"
        elif os.name == "posix":
            if platform.mac_ver()[0]:
                path = name + release + ".dylib"
            else:
                path = "lib" + name + release + ".so"
        else:
            sys.stderr.write("Cannot find library " + name + "\n")
 
        # In most cases, you actually don't want the above and instead
        # use the exact filename within your game distribution, possibly
        # even within a .zip file.
        # if not os.path.exists(path):
        #     path = "dlls/" + path

    try:
        # RTLD_GLOBAL is required under OSX for some reason (?)
        _dlls.append(CDLL(path, RTLD_GLOBAL))
    except OSError:
        # No need to fail here, might just be one of the addons.
        pass
      
   # os.chdir("..")

_add_dll("allegro")
_add_dll("allegro_acodec")
_add_dll("allegro_audio")
_add_dll("allegro_primitives")
_add_dll("allegro_color")
_add_dll("allegro_font")
_add_dll("allegro_ttf")
_add_dll("allegro_image")
_add_dll("allegro_dialog")
_add_dll("allegro_memfile")
_add_dll("allegro_physfs")
_add_dll("allegro_shader")
_add_dll("allegro_main")

# We don't have information ready which A5 function is in which DLL,
# so we just try them all.
def _dll(func, ret, params):
    for dll in _dlls:
        try:
            f = dll[func]
            f.restype = ret
            f.argtypes = params
            return f
        except AttributeError: pass
    sys.stderr.write("Cannot find function " + func + "\n")
    return lambda *args: None

# In Python3, all Python strings are unicode so we have to convert to
# UTF8 byte strings before passing to Allegro.
if sys.version_info[0] > 2:
    class _AL_UTF8String:
        def from_param(x):
            return x.encode("utf8")
else:
    _AL_UTF8String = c_char_p

""" % locals())

    for name, val in sorted(al.constants.items()):
        f.write(name + " = " + str(val) + "\n")

    for name, x in sorted(al.types.items()):
        if not name: continue
        base = x.__bases__[0]
        if base != Structure and base != Union:
            f.write(name + " = " + x.__name__ + "\n")

    for kind in Structure, Union:
        for name, x in sorted(al.types.items()):
            if not x: continue
            base = x.__bases__[0]
            if base != kind: continue
            f.write("class " + name + "(" + base.__name__ + "): pass\n")
            pt = POINTER(x)
            f.write("%s = POINTER(%s)\n" % (pt.__name__, name))

        for name, x in sorted(al.types.items()):
            base = x.__bases__[0]
            if base != kind: continue
            if hasattr(x, "my_fields"):
                f.write(name + "._fields_ = [\n")
                for fname, ftype in x.my_fields:
                     f.write("    (\"" + fname + "\", " + ftype + "),\n")
                f.write("    ]\n")

    for name, x in sorted(al.functions.items()):
        try:
            line = name + " = _dll(\"" + name + "\", "
            line += x.restype.__name__ + ", "
            line += "[" + (", ".join([a.__name__ for a in x.argtypes])) +\
                "])\n"
            f.write(line)
        except AttributeError as e:
            print("Ignoring " + name + " because of errors (" + str(e) + ").")

    # some stuff the automated parser doesn't pick up
    f.write(r"""
ALLEGRO_VERSION_INT = \
    ((ALLEGRO_VERSION << 24) | (ALLEGRO_SUB_VERSION << 16) | \
    (ALLEGRO_WIP_VERSION << 8) | ALLEGRO_RELEASE_NUMBER)
    """)
    
    f.write(r"""
# work around bug http://gcc.gnu.org/bugzilla/show_bug.cgi?id=36834
if os.name == "nt":
    def al_map_rgba_f(r, g, b, a): return ALLEGRO_COLOR(r, g, b, a)
    def al_map_rgb_f(r, g, b): return ALLEGRO_COLOR(r, g, b, 1)
    def al_map_rgba(r, g, b, a): return ALLEGRO_COLOR(r / 255.0, g / 255.0, b / 255.0, a / 255.0)
    def al_map_rgb(r, g, b): return ALLEGRO_COLOR(r / 255.0, g / 255.0, b / 255.0, 1)
    """)

    f.write("""
def al_main(real_main, *args):
    def python_callback(argc, argv):
        real_main(*args)
        return 0
    cb = CFUNCTYPE(c_int, c_int, c_void_p)(python_callback)
    al_run_main(0, 0, cb);
""")

    f.close()

main()
