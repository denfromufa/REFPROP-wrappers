"""
Important note on windows: you may need to manually copy the libmsvcrXXX.a files to libmsvcrXXXd.a 
"""
import os
import glob
import sys
import re
import six
from subprocess import Popen, PIPE
import subprocess
import textwrap
import time

def gen_pyf(fortran_root):

    # Call f2py to generate .pyf file
    interface_files = ['PASS_FTN.FOR']
    interface_file_strings = [os.path.join(fortran_root, f) for f in interface_files]
    pyf_file = 'f2pyREFPROP.pyf'
    args = ['python','-m','numpy.f2py','--quiet','-h',pyf_file,'-m','f2pyREFPROP','--overwrite-signature','--no-lower'] + interface_file_strings
    p = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    output, err = p.communicate()
    rc = p.returncode
    with open('stdout.txt','w') as fp:
        fp.write(output.decode('utf-8'))
    with open('stderr.txt','w') as fp:
        fp.write(err.decode('utf-8'))
    with open(pyf_file) as fp:
        pyf = fp.read()
    os.remove(pyf_file)
    return pyf
    
wrapper_header = """

from __future__ import print_function
import ctypes as ct
import sys, os, glob
from collections import namedtuple

def trim(s):
    return s.replace(b'\\x00',b'').strip().decode('utf-8')

class REFPROPFunctionLibrary():

    def _getfcn(self, DLL, fname):
        \"\"\"
        Try to obtain a function pointer to the function in the shared library.  
        If you cannot attach the function, send a warning, and return None.  

        Warnings can be disabled with something like:

        import warnings
        warnings.filterwarnings("ignore")

        or consult the python docs
        \"\"\"
        try:
            return getattr(DLL, fname)
        except:
            return None

    def __init__(self, name, shared_extension = None):
        \"\"\"
        You can either provide a path to a directory, in which case it will search 
        for the necessary DLL in that directory, or an absolute path to a shared library

        Parameters
        ==========
        name : str 
            The name, either of a folder or a file
        shared_extension : str
            The extension that should be queried when searching for shared libraries.  
            Uses the architecture-specific file extension by default
        \"\"\"

        if sys.platform.startswith('win'):
            loader_fcn = ct.WinDLL
        else:
            loader_fcn = ct.CDLL

        # An absolute path to a file was provided, we will use it
        if os.path.isfile(name):
            full_path = name
        # If the provided string is a path, then we use it to find any shared libraries
        elif os.path.isdir(name):
            # Determine the shared library extension
            if shared_extension is None:
                if sys.platform.startswith('win'):
                    shared_extension = 'dll'
                elif sys.platform.startswith('darwin'):
                    shared_extension = 'dylib'
                else:
                    shared_extension = 'so'

            sos = glob.glob(os.path.join(name, '*.' + shared_extension))
            if len(sos) == 0:
                raise ValueError('No shared libraries were found in the folder "{name:s}" with the extension ".{ext:s}"'.format(ext=shared_extension,name=name))
            elif len(sos) == 1:
                full_path = sos[0]
            else:
                good_so = []
                for so in sos:
                    try:
                        trash = loader_fcn(so)
                        good_so.append(so)
                        del trash
                    except BaseException as BE:
                        pass # do nothing, we just won't keep this shared library
                if len(good_so) == 0:
                    raise ValueError('No shared libraries were found in the folder "{name:s}" with the extension ".{ext:s}"'.format(ext=shared_extension,name=name))
                elif len(good_so) == 1:
                    full_path = good_so[0]
                else:
                    raise ValueError('Too many loadable shared libraries were found in the folder "{name:s}"; obtained libraries were: {libs:s}'.format(name=name, libs=str(list(good_so))))
        else:
            raise ValueError('"{name:s}" is neither a directory nor a file'.format(name=name))

        # Now make it, set it, we're done
        self.dll = loader_fcn(full_path)

"""

def gen_wrapper(pyf):
    # grab each function in the PYF
    
    regex = r"subroutine\s{1}(.*?)end subroutine"
    matches = re.finditer(regex, pyf, re.DOTALL)

    fcninfo = {}
    for match in matches:

        captured_lines = match.group(1).split('\n') # 1 for "use the first captured group"; 0 is the entire match
        # Parse the header line
        head = captured_lines[0].split('!')[0].strip() # Keep everything left of '!'
        argnames = []
        if '(' in head:
            fcnname = head.split('(')[0].strip()
            argnames = head.split('(')[1].split(')')[0].split(',')
        else:
            fcnname = head.strip()

        input_args = {}
        inout_args = {}
        output_args = {}
        # Parse the argument types
        for line in captured_lines[1::]:
            if not line.strip(): continue
            if not 'intent(' in line: continue # Skip local variables
            if 'intent(c)' in line: continue # Skip strings
            argname = line.split('::')[1].strip()
            datatype = ()
            dim = 0
            if 'dimension' in line:
                # find the dimension
                match = re.findall(r'dimension\((.*?)\)',line)
                dim = int(match[0])

            if 'integer' in line:
                datatype = ('int', dim)
            elif 'double precision' in line:
                datatype = ('double', dim)
            elif 'character' in line:
                match = re.findall(r'character\*(.*?)[\,\s]',line)
                if match[0] == r'(*)':
                    dim = '*'
                else:
                    dim = int(match[0])
                datatype = ('char', dim)
            else:
                raise KeyError('Unable to determine the datatype for line: '+line)

            # What kind is it
            if 'intent(in)' in line:
                input_args[argname] = datatype
            elif 'intent(out)' in line:
                output_args[argname] = datatype
            elif 'intent(inout)' in line:
                inout_args[argname] = datatype
            else:
                raise ValueError('intent not set in this line: '+line)
            
        fcninfo[fcnname] = dict(argnames = argnames, input_args = input_args, inout_args = inout_args, output_args = output_args)
    return fcninfo

file_docstring = '''"""
This file was auto-generated by the script:
https://github.com/usnistgov/REFPROP-wrappers/wrappers/python/ctypes/build_wrapper.py
on {datetime:s}.  This interface was written by Ian Bell. 

If you have any problems, please file an issue at:
https://github.com/usnistgov/REFPROP-wrappers/issues/
"""
'''

def gen_ctypes_wrappers(fcninfo, ofname):

    file_header = file_docstring.format(datetime=time.strftime("%d %b %Y %H:%M:%S", time.localtime()))

    contents = ''
    function_pointer_string = ''
    namedtuples_header = ' '*8 + '# Named tuples to contain the outputs of DLL calls\n'

    for fcn, data in sorted(six.iteritems(fcninfo)):

        function_pointer_string += ' '*8 + 'self._{name:s} = self._getfcn(self.dll, \'{name:s}\')\n'.format(name=fcn)

        i, o = [], []
        for arg in data['argnames']:
            if arg in data['input_args']:
                i.append(arg)
            elif arg in data['output_args']:
                o.append(arg)
            elif arg in data['inout_args']:
                i.append(arg)
                o.append(arg)

        proto = ','.join(o)+ ' = '+fcn+'(' + ','.join(i) + ')'
        headline = 'def {fname:s}(self,{inargs:s}):\n'.format(inargs=','.join(i), fname = fcn)
        body = '\"\"\"\n{proto:s}\n\"\"\"\n'.format(proto=proto)

        def gen_val(typ, dim, default = ''):
            if typ == 'int' and dim == 0:
                return 'ct.c_long({default:s})'.format(default=default)
            elif typ == 'int' and dim > 0:
                return '({dim:d}*ct.c_long)()'.format(default=default, dim=dim)
            elif typ == 'double' and dim == 0:
                return 'ct.c_double({default:s})'.format(default=default)
            elif typ == 'double' and dim < 0:
                return '(len({default:s})*ct.c_double)(*{default:s})'.format(default=default)
            elif typ == 'double' and dim > 0:
                return '({dim:d}*ct.c_double)()'.format(default=default, dim=dim)
            elif typ == 'char' and dim != 0 and default:
                return 'ct.create_string_buffer({default:s}.encode(\'utf-8\'),{dim:d})'.format(default=default, dim = abs(dim))
            elif typ == 'char' and dim != 0 and not default:
                return 'ct.create_string_buffer({dim:d})'.format(dim = abs(dim))
            else:
                raise KeyError('Invalid type/dim pair:'+str((typ,dim)))

        # Add the type definitions
        for arg in data['argnames']:
            if arg in data['input_args'] or arg in data['inout_args']:
                typ, dim = data['input_args'].get(arg,None) or data['inout_args'].get(arg, None)
                if dim == '*':
                    body += '{name:s} = '.format(name=arg) + 'ct.create_string_buffer({default:s},len{default:s})'.format(default=arg) + '\n'
                else:
                    body += '{name:s} = '.format(name=arg) + gen_val(typ, -dim, default = arg) + '\n'
            elif arg in data['output_args']:
                typ, dim = data['output_args'][arg]
                body += '{name:s} = '.format(name=arg) + gen_val(typ, dim, default = '') + '\n'

        # Add the function call
        string_lengths = []
        arg_strings = []
        for arg in data['argnames']:
            typ, dim = data['input_args'].get(arg,None) or data['inout_args'].get(arg, None) or data['output_args'].get(arg, None)
            if dim == 0 and typ in ['int','double']:
                arg_strings.append('ct.byref({arg:s})'.format(arg=arg))
            else:
                arg_strings.append(arg)
            if typ == 'char':
                if dim == '*':
                    string_lengths.append(str(10000))
                else:
                    string_lengths.append(str(dim))

        body += '\nif self._{fcnname:s} is None: raise ValueError("The function {fcnname:s} could not be loaded from the shared library.") \n'.format(fcnname=fcn)
        if len(string_lengths) == 0:
            string_args = ''
        else:
            string_args = ',' + ','.join(string_lengths)

        body += 'self._{fcnname:s}({args:s}{string_args:s})\n'.format(fcnname=fcn, args = ','.join(arg_strings), string_args=string_args) + '\n'

        # Add the return call
        arg_strings = []
        arg_names = []
        for arg in data['argnames']:
            if arg in data['output_args'] or arg in data['inout_args']:
                typ, dim = data['output_args'].get(arg,None) or data['inout_args'].get(arg,None)
                arg_names.append('"' + arg + '"')
                if dim == 0 and typ in ['int','double']:
                    arg_strings.append('{arg:s}.value'.format(arg=arg))
                elif dim > 0 and typ in ['int','double']:
                    arg_strings.append('list({arg:s})'.format(arg=arg))
                elif typ == 'char':
                    arg_strings.append('trim({arg:s}.raw)'.format(arg=arg))
                else:
                    arg_strings.append(arg)
        if len(arg_strings) > 1:
            namedtuples_header += ' '*8 + 'self._{fcn:s}output_tuple = namedtuple(\'{fcn:s}output\',[{argnames:s}])\n'.format(fcn=fcn,  argnames = ','.join(arg_names))
            body += 'return self._{fcn:s}output_tuple({args:s})'.format(fcn=fcn, args=','.join(arg_strings)) + '\n\n'
        elif len(arg_strings) == 1:
            body += 'return {arg:s}'.format(arg=arg_strings[0]) + '\n\n'
        else:
            body += 'return\n\n'
        
        contents += textwrap.indent(headline,' '*4) + textwrap.indent(body,' '*8)

    # Write it into the output file
    with open(ofname, 'w') as fp:
        fp.write(file_header)
        fp.write(wrapper_header)
        fp.write(namedtuples_header + '\n')
        fp.write(function_pointer_string + '\n')
        fp.write(contents)

if __name__=='__main__':
    fortran_root = r'D:\Code\REFPROP-cmake\FORTRANsrc'
    pyf = gen_pyf(fortran_root)
    with open('data.pyf','w') as fp:
        fp.write(pyf)
    fcninfo = gen_wrapper(pyf)
    if not os.path.exists('ctREFPROP'): os.mkdir(os.path.join(os.path.dirname(__file__), 'ctREFPROP'))
    gen_ctypes_wrappers(fcninfo, os.path.join('ctREFPROP', 'ctREFPROP.py'))