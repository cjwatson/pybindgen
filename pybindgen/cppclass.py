"""
Wrap C++ classes and methods
"""

import warnings

from typehandlers.base import ForwardWrapperBase, Parameter, ReturnValue

from cppmethod import CppMethod, CppConstructor, CppNoConstructor, \
    CppOverloadedMethod, CppOverloadedConstructor
from cppattribute import (CppInstanceAttributeGetter, CppInstanceAttributeSetter,
                          CppStaticAttributeGetter, CppStaticAttributeSetter,
                          PyGetSetDef, PyMetaclass)
import settings


class CppClass(object):
    """
    A CppClass object takes care of generating the code for wrapping a C++ class
    """

    TYPE_TMPL = (
        'PyTypeObject %(typestruct)s = {\n'
        '    PyObject_HEAD_INIT(NULL)\n'
        '    0,                                 /* ob_size */\n'
        '    "%(classname)s",                   /* tp_name */\n'
        '    %(tp_basicsize)s,                  /* tp_basicsize */\n'
        '    0,                                 /* tp_itemsize */\n'
        '    /* methods */\n'
        '    (destructor)%(tp_dealloc)s,        /* tp_dealloc */\n'
        '    (printfunc)0,                      /* tp_print */\n'
        '    (getattrfunc)%(tp_getattr)s,       /* tp_getattr */\n'
        '    (setattrfunc)%(tp_setattr)s,       /* tp_setattr */\n'
        '    (cmpfunc)%(tp_compare)s,           /* tp_compare */\n'
        '    (reprfunc)%(tp_repr)s,             /* tp_repr */\n'
        '    (PyNumberMethods*)%(tp_as_number)s,     /* tp_as_number */\n'
        '    (PySequenceMethods*)%(tp_as_sequence)s, /* tp_as_sequence */\n'
        '    (PyMappingMethods*)%(tp_as_mapping)s,   /* tp_as_mapping */\n'
        '    (hashfunc)%(tp_hash)s,             /* tp_hash */\n'
        '    (ternaryfunc)%(tp_call)s,          /* tp_call */\n'
        '    (reprfunc)%(tp_str)s,              /* tp_str */\n'
        '    (getattrofunc)%(tp_getattro)s,     /* tp_getattro */\n'
        '    (setattrofunc)%(tp_setattro)s,     /* tp_setattro */\n'
        '    (PyBufferProcs*)%(tp_as_buffer)s,  /* tp_as_buffer */\n'
        '    %(tp_flags)s,                      /* tp_flags */\n'
        '    %(tp_doc)s,                        /* Documentation string */\n'
        '    (traverseproc)%(tp_traverse)s,     /* tp_traverse */\n'
        '    (inquiry)%(tp_clear)s,             /* tp_clear */\n'
        '    (richcmpfunc)%(tp_richcompare)s,   /* tp_richcompare */\n'
        '    %(tp_weaklistoffset)s,             /* tp_weaklistoffset */\n'
        '    (getiterfunc)%(tp_iter)s,          /* tp_iter */\n'
        '    (iternextfunc)%(tp_iternext)s,     /* tp_iternext */\n'
        '    (struct PyMethodDef*)%(tp_methods)s, /* tp_methods */\n'
        '    (struct PyMemberDef*)0,              /* tp_members */\n'
        '    %(tp_getset)s,                     /* tp_getset */\n'
        '    NULL,                              /* tp_base */\n'
        '    NULL,                              /* tp_dict */\n'
        '    (descrgetfunc)%(tp_descr_get)s,    /* tp_descr_get */\n'
        '    (descrsetfunc)%(tp_descr_set)s,    /* tp_descr_set */\n'
        '    %(tp_dictoffset)s,                 /* tp_dictoffset */\n'
        '    (initproc)%(tp_init)s,             /* tp_init */\n'
        '    (allocfunc)%(tp_alloc)s,           /* tp_alloc */\n'
        '    (newfunc)%(tp_new)s,               /* tp_new */\n'
        '    (freefunc)%(tp_free)s,             /* tp_free */\n'
        '    (inquiry)%(tp_is_gc)s,             /* tp_is_gc */\n'
        '    NULL,                              /* tp_bases */\n'
        '    NULL,                              /* tp_mro */\n'
        '    NULL,                              /* tp_cache */\n'
        '    NULL,                              /* tp_subclasses */\n'
        '    NULL,                              /* tp_weaklist */\n'
        '    (destructor) NULL                  /* tp_del */\n'
        '};\n'
        )

    def __init__(self, name, parent=None, incref_method=None, decref_method=None,
                 automatic_type_narrowing=None):
        """Constructor
        name -- class name
        parent -- optional parent class wrapper
        incref_method -- if the class supports reference counting, the
                         name of the method that increments the
                         reference count (may be inherited from parent
                         if not given)
        decref_method -- if the class supports reference counting, the
                         name of the method that decrements the
                         reference count (may be inherited from parent
                         if not given)
        automatic_type_narrowing -- if True, automatic return type
                                    narrowing will be done on objects
                                    of this class and its descendents
                                    when returned by pointer from a
                                    function or method.
        """
        self.name = name
        self.methods = {} # name => OverloadedMethod
        self.constructors = [] # (name, wrapper) pairs
        self.slots = dict()

        prefix = settings.name_prefix.capitalize()
        self.pystruct = "Py%s%s" % (prefix, self.name)
        self.metaclass_name = "%sMeta" % self.pystruct
        self.pytypestruct = "Py%s%s_Type" % (prefix, self.name)

        self.instance_attributes = PyGetSetDef("%s__getsets" % self.pystruct)
        self.static_attributes = PyGetSetDef("%s__getsets" % self.metaclass_name)

        self.parent = parent
        assert parent is None or isinstance(parent, CppClass)
        assert (incref_method is None and decref_method is None) \
               or (incref_method is not None and decref_method is not None)
        if incref_method is None and parent is not None:
            self.incref_method = parent.incref_method
            self.decref_method = parent.decref_method
        else:
            self.incref_method = incref_method
            self.decref_method = decref_method

        if automatic_type_narrowing is None:
            if parent is None:
                self.automatic_type_narrowing = settings.automatic_type_narrowing
            else:
                self.automatic_type_narrowing = parent.automatic_type_narrowing
        else:
            self.automatic_type_narrowing = automatic_type_narrowing

        self.typeid_map = None
        self.typeid_map_name = None # name of C++ variable
        if self.automatic_type_narrowing:
            self._register_typeid()

        if name != 'dummy':
            ## register type handlers
            class ThisClassParameter(CppClassParameter):
                """Register this C++ class as pass-by-value parameter"""
                CTYPES = [name]
                cpp_class = self
            class ThisClassRefParameter(CppClassRefParameter):
                """Register this C++ class as pass-by-reference parameter"""
                CTYPES = [name+'&']
                cpp_class = self
            class ThisClassReturn(CppClassReturnValue):
                """Register this C++ class as value return"""
                CTYPES = [name]
                cpp_class = self
            class ThisClassPtrParameter(CppClassPtrParameter):
                """Register this C++ class as pass-by-pointer parameter"""
                CTYPES = [name+'*']
                cpp_class = self
            class ThisClassPtrReturn(CppClassPtrReturnValue):
                """Register this C++ class as pointer return"""
                CTYPES = [name+'*']
                cpp_class = self

    
    def get_type_narrowing_root(self):
        """Find the root CppClass along the subtree of all parent classes that
        have automatic_type_narrowing=True Note: multiple inheritance
        not implemented"""
        root = self
        while (root.parent is not None
               and root.parent.automatic_type_narrowing):
            root = root.parent
        return root

    def _register_typeid(self):
        """register this class with the typeid map root class"""
        root = self.get_type_narrowing_root()
        if root is self:
            ## since we are the root, we are responsible for creating
            ## and managing the typeid table
            self.typeid_map = [self] # list of registered subclasses
            self.typeid_map_name = "%s__typeid_map" % self.pystruct
        else:
            root.typeid_map.append(self)

    def _generate_typeid_map(self, code_sink, module):
        """generate the typeid map and fill it with values"""
        module.add_include("<map>")
        module.header.writeln("\nstd::map<const char *, PyTypeObject *> %s;\n\n"
                              % self.typeid_map_name)
        for subclass in self.typeid_map:
            module.after_init.write_code("%s[typeid(%s).name()] = &%s;"
                                         % (self.typeid_map_name, subclass.name,
                                            subclass.pytypestruct))

    def add_method(self, wrapper, name=None):
        """
        Add a method to the class.

        wrapper -- a CppMethod instance that can generate the wrapper
        name -- optional name of the class method as it will appear
                from Python side
        """
        assert name is None or isinstance(name, str)
        assert isinstance(wrapper, CppMethod)
        if name is None:
            name = wrapper.method_name
            
        try:
            overload = self.methods[name]
        except KeyError:
            overload = CppOverloadedMethod(name)
            overload.pystruct = self.pystruct
            self.methods[name] = overload

        wrapper.class_ = self
        overload.add(wrapper)


    def add_constructor(self, wrapper):
        """
        Add a constructor to the class.

        wrapper -- a CppConstructor instance
        """
        assert isinstance(wrapper, CppConstructor)
        wrapper.set_class(self)
        self.constructors.append(wrapper)

    def add_static_attribute(self, value_type, name, is_const=False):
        """
        Caveat: static attributes cannot be changed from Python; not implemented.
        value_type -- a ReturnValue object
        name -- attribute name (i.e. the name of the class member variable)
        is_const -- True if the attribute is const, i.e. cannot be modified
        """
        assert isinstance(value_type, ReturnValue)
        getter = CppStaticAttributeGetter(value_type, self, name)
        if is_const:
            setter = None
        else:
            setter = CppStaticAttributeSetter(value_type, self, name)
        self.static_attributes.add_attribute(name, getter, setter)

    def add_instance_attribute(self, value_type, name, is_const=False):
        """
        value_type -- a ReturnValue object
        name -- attribute name (i.e. the name of the class member variable)
        is_const -- True if the attribute is const, i.e. cannot be modified
        """
        assert isinstance(value_type, ReturnValue)
        getter = CppInstanceAttributeGetter(value_type, self, name)
        if is_const:
            setter = None
        else:
            setter = CppInstanceAttributeSetter(value_type, self, name)
        self.instance_attributes.add_attribute(name, getter, setter)

    def generate_forward_declarations(self, code_sink):
        """Generates forward declarations for the instance and type
        structures"""

        code_sink.writeln('''
typedef struct {
    PyObject_HEAD
    %s *obj;
} %s;
''' % (self.name, self.pystruct))

        code_sink.writeln()
        code_sink.writeln('extern PyTypeObject %s;' % (self.pytypestruct,))
        code_sink.writeln()
        

    def generate(self, code_sink, module, docstring=None):
        """Generates the class to a code sink"""

        ## generate getsets
        instance_getsets = self.instance_attributes.generate(code_sink)
        self.slots.setdefault("tp_getset", instance_getsets)
        static_getsets = self.static_attributes.generate(code_sink)

        ## --- register the class type in the module ---
        module.after_init.write_code("/* Register the '%s' class */" % self.name)

        ## generate a metaclass if needed
        if static_getsets == '0':
            metaclass = None
        else:
            if self.parent is None:
                parent_typestruct = 'PyBaseObject_Type'
            else:
                parent_typestruct = self.parent.pytypestruct
            metaclass = PyMetaclass(self.metaclass_name,
                                    "%s.ob_type" % parent_typestruct,
                                    self.static_attributes)
            metaclass.generate(code_sink, module)

        if self.parent is not None:
            assert isinstance(self.parent, CppClass)
            module.after_init.write_code('%s.tp_base = &%s;' %
                                         (self.pytypestruct, self.parent.pytypestruct))

        if metaclass is not None:
            module.after_init.write_code('%s.ob_type = &%s;' %
                                         (self.pytypestruct, metaclass.pytypestruct))

        module.after_init.write_error_check('PyType_Ready(&%s)'
                                          % (self.pytypestruct,))
        module.after_init.write_code(
            'PyModule_AddObject(m, \"%s\", (PyObject *) &%s);' % (
            self.name, self.pytypestruct))

        have_constructor = self._generate_constructor(code_sink)
        self._generate_methods(code_sink)
        self._generate_destructor(code_sink, have_constructor)
        self._generate_type_structure(code_sink, docstring)
        if self.typeid_map is not None:
            self._generate_typeid_map(code_sink, module)

        
    def _generate_type_structure(self, code_sink, docstring):
        """generate the type structure"""
        self.slots.setdefault("tp_basicsize",
                              "sizeof(%s)" % (self.pystruct,))
        for slot in ["tp_getattr", "tp_setattr", "tp_compare", "tp_repr",
                     "tp_as_number", "tp_as_sequence", "tp_as_mapping",
                     "tp_hash", "tp_call", "tp_str", "tp_getattro", "tp_setattro",
                     "tp_as_buffer", "tp_traverse", "tp_clear", "tp_richcompare",
                     "tp_iter", "tp_iternext", "tp_descr_get",
                     "tp_descr_set", "tp_is_gc"]:
            self.slots.setdefault(slot, "NULL")

        self.slots.setdefault("tp_dictoffset", "0")
        self.slots.setdefault("tp_alloc", "PyType_GenericAlloc")
        self.slots.setdefault("tp_new", "PyType_GenericNew")
        self.slots.setdefault("tp_free", "_PyObject_Del")
        self.slots.setdefault("tp_weaklistoffset", "0")
        self.slots.setdefault("tp_flags", "Py_TPFLAGS_DEFAULT")
        self.slots.setdefault("tp_doc", (docstring is None and 'NULL'
                                         or "\"%s\"" % (docstring,)))
        dict_ = dict(self.slots)
        dict_.setdefault("typestruct", self.pytypestruct)
        dict_.setdefault("classname", self.name)
        
        code_sink.writeln(self.TYPE_TMPL % dict_)


    def _generate_constructor(self, code_sink):
        """generate the constructor, if any"""
        have_constructor = True
        if self.constructors:
            code_sink.writeln()
            overload = CppOverloadedConstructor(None)
            overload.pystruct = self.pystruct
            for constructor in self.constructors:
                overload.add(constructor)
            overload.generate(code_sink)
            constructor = overload.wrapper_function_name
            code_sink.writeln()
        else:
            ## if there is a parent constructor with no arguments,
            ## a similar constructor should be added to this class
            if self.parent is None:
                parent_default_constructor = None
            else:
                parent_default_constructor = self.parent.get_default_constructor()
            if parent_default_constructor is not None:
                cons = CppConstructor([])
                cons.set_class(self)
                code_sink.writeln()
                cons.generate(code_sink)
                constructor = cons.wrapper_actual_name
                code_sink.writeln()
            else:
                ## In C++, and unlike Python, constructors with
                ## parameters are not automatically inheritted by
                ## subclasses.  We must generate a 'no constructor'
                ## tp_init to prevent this type from inheriring a
                ## tp_init that will allocate an instance of the
                ## parent class instead of this class.
                code_sink.writeln()
                constructor = CppNoConstructor().generate(code_sink, self)
                have_constructor = False
                code_sink.writeln()
        self.slots.setdefault("tp_init", (constructor is None and "NULL"
                                          or constructor))
        return have_constructor

    def get_default_constructor(self):
        """get the default constructor for this class according to C++
        language rules"""
        for cons in self.constructors:
            if len(cons.parameters) == 0:
                return cons
        if self.parent is not None:
            return self.parent.get_default_constructor
        return None

    def _generate_methods(self, code_sink):
        """generate the method wrappers"""
        method_defs = []
        for meth_name, overload in self.methods.iteritems():
            code_sink.writeln()
            overload.generate(code_sink)
            method_defs.append(overload.get_py_method_def(meth_name))
            code_sink.writeln()
        ## generate the method table
        code_sink.writeln("static PyMethodDef %s_methods[] = {" % (self.name,))
        code_sink.indent()
        for methdef in method_defs:
            code_sink.writeln(methdef)
        code_sink.writeln("{NULL, NULL, 0, NULL}")
        code_sink.unindent()
        code_sink.writeln("};")
        self.slots.setdefault("tp_methods", "%s_methods" % (self.name,))


    def _generate_destructor(self, code_sink, have_constructor):
        """Generate a tp_dealloc function and register it in the type"""

        ## don't generate destructor if overridden by user
        if "tp_dealloc" in self.slots:
            return

        tp_dealloc_function_name = "_wrap_%s__tp_dealloc" % (self.pystruct,)
        if have_constructor:
            if self.decref_method is None:
                delete_code = "delete tmp;"
            else:
                delete_code = ("if (tmp)\n        tmp->%s()"
                               % (self.decref_method,))
            code_sink.writeln('''
static void
%s(%s *self)
{
    %s *tmp = self->obj;
    self->obj = NULL;
    %s;
    PyObject_DEL(self);
}
    ''' % (tp_dealloc_function_name, self.pystruct, self.name, delete_code))

        else: # don't have constructor

            code_sink.writeln('''
static void
%s(%s *self)
{
    PyObject_DEL(self);
}
    ''' % (tp_dealloc_function_name, self.pystruct))
        code_sink.writeln()
        self.slots.setdefault("tp_dealloc", tp_dealloc_function_name )


class CppClassParameter(Parameter):
    "Class handlers"
    CTYPES = []
    cpp_class = CppClass('dummy') # CppClass instance
    DIRECTIONS = [Parameter.DIRECTION_IN]
    
    def convert_python_to_c(self, wrapper):
        "parses python args to get C++ value"
        assert isinstance(wrapper, ForwardWrapperBase)
        assert isinstance(self.cpp_class, CppClass)
        name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', self.name)
        wrapper.parse_params.add_parameter(
            'O!', ['&'+self.cpp_class.pytypestruct, '&'+name], self.name)
        wrapper.call_params.append(
            '*((%s *) %s)->obj' % (self.cpp_class.pystruct, name))


class CppClassRefParameter(Parameter):
    "Class& handlers"
    CTYPES = []
    cpp_class = CppClass('dummy') # CppClass instance
    DIRECTIONS = [Parameter.DIRECTION_IN,
                  Parameter.DIRECTION_OUT,
                  Parameter.DIRECTION_INOUT]
    
    def convert_python_to_c(self, wrapper):
        "parses python args to get C++ value"
        assert isinstance(wrapper, ForwardWrapperBase)
        assert isinstance(self.cpp_class, CppClass)

        name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', self.name)

        if self.direction == Parameter.DIRECTION_IN:
            wrapper.parse_params.add_parameter(
                'O!', ['&'+self.cpp_class.pytypestruct, '&'+name], self.name)
            wrapper.call_params.append('*%s->obj' % (name,))

        elif self.direction == Parameter.DIRECTION_OUT:
            wrapper.before_call.write_code(
                "%s = PyObject_New(%s, %s);" %
                (name, self.cpp_class.pystruct, '&'+self.cpp_class.pytypestruct))
            wrapper.before_call.write_code(
                "%s->obj = new %s;" % (name, self.cpp_class.name))
            wrapper.call_params.append('*%s->obj' % (name,))
            wrapper.build_params.add_parameter("N", [name])

        ## well, personally I think inout here doesn't make much sense
        ## (it's just plain confusing), but might as well support it..
        elif self.direction == Parameter.DIRECTION_INOUT:
            wrapper.parse_params.add_parameter(
                'O!', ['&'+self.cpp_class.pytypestruct, '&'+name], self.name)
            wrapper.call_params.append(
                '*%s->obj' % (name))
            wrapper.build_params.add_parameter("O", [name])


class CppClassReturnValue(ReturnValue):
    "Class return handlers"
    CTYPES = []
    cpp_class = CppClass('dummy') # CppClass instance

    def get_c_error_return(self): # only used in reverse wrappers
        """See ReturnValue.get_c_error_return"""
        return "return %s();" % (self.cpp_class.name,)

    def convert_c_to_python(self, wrapper):
        """see ReturnValue.convert_c_to_python"""
        py_name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', 'py_'+self.cpp_class.name)
        wrapper.after_call.write_code(
            "%s = PyObject_New(%s, %s);" %
            (py_name, self.cpp_class.pystruct, '&'+self.cpp_class.pytypestruct))
        wrapper.after_call.write_code(
            "%s->obj = new %s(%s);" % (py_name, self.cpp_class.name, self.value))
        wrapper.build_params.add_parameter("N", [py_name], prepend=True)

    def convert_python_to_c(self, wrapper):
        """see ReturnValue.convert_python_to_c"""
        name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', "tmp_%s" % self.cpp_class.name)
        wrapper.parse_params.add_parameter(
            'O!', ['&'+self.cpp_class.pytypestruct, '&'+name])
        wrapper.after_call.write_code('%s = *%s->obj;' % (self.value, name))
    


class CppClassPtrParameter(Parameter):
    "Class* handlers"
    CTYPES = []
    cpp_class = CppClass('dummy') # CppClass instance
    DIRECTIONS = [Parameter.DIRECTION_IN]
    SUPPORTS_TRANSFORMATIONS = True

    def __init__(self, ctype, name, transfer_ownership):
        """
        ctype -- C type, normally 'MyClass*'
        name -- parameter name
        transfer_ownership -- this parameter transfer the ownership of
                              the pointed-to object to the called function
        """
        super(CppClassPtrParameter, self).__init__(
            ctype, name, direction=Parameter.DIRECTION_IN)
        self.transfer_ownership = transfer_ownership
        
    def convert_python_to_c(self, wrapper):
        "parses python args to get C++ value"
        assert isinstance(wrapper, ForwardWrapperBase)
        assert isinstance(self.cpp_class, CppClass)
        name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', self.name)
        wrapper.parse_params.add_parameter(
            'O!', ['&'+self.cpp_class.pytypestruct, '&'+name], self.name)

        value = self.transformation.transform(
            self, wrapper.declarations, wrapper.before_call, '%s->obj' % name)
        wrapper.call_params.append(value)
        
        if self.transfer_ownership:
            if self.cpp_class.incref_method is None:
                wrapper.after_call.write_code('%s->obj = NULL;' % (name,))
            else:
                wrapper.before_call.write_code('%s->obj->%s();' % (
                    name, self.cpp_class.incref_method,))


class CppClassPtrReturnValue(ReturnValue):
    "Class* return handler"
    CTYPES = []
    SUPPORTS_TRANSFORMATIONS = True
    cpp_class = CppClass('dummy') # CppClass instance

    def __init__(self, ctype, caller_owns_return):
        """
        ctype -- C type, normally 'MyClass*'
        caller_owns_return -- if true, ownership of the object pointer
                              is transfered to the caller
        """
        super(CppClassPtrReturnValue, self).__init__(ctype)
        self.caller_owns_return = caller_owns_return

    def get_c_error_return(self): # only used in reverse wrappers
        """See ReturnValue.get_c_error_return"""
        return "return NULL;"

    def convert_c_to_python(self, wrapper):
        """See ReturnValue.convert_c_to_python"""

        ## Value transformations
        value = self.transformation.untransform(
            self, wrapper.declarations, wrapper.after_call, self.value)

        ## Find out what Python wrapper to use, in case
        ## automatic_type_narrowing is active and we are not forced to
        ## make a copy of the object
        if (self.cpp_class.automatic_type_narrowing
            and (self.caller_owns_return or self.cpp_class.incref_method is not None)):
            wrapper_type = wrapper.declarations.declare_variable('PyTypeObject*',
                                                                 'wrapper_type')
            wrapper.after_call.write_code(
                "%s = %s[typeid(*%s).name()];" %
                (wrapper_type,
                 self.cpp_class.get_type_narrowing_root().typeid_map_name,
                 value))
            ## fallback to the base type in case of typeid map lookup failure
            wrapper.after_call.write_code("if (!%s)" % wrapper_type)
            wrapper.after_call.indent()
            wrapper.after_call.write_code("%s = &%s;" %
                                          (wrapper_type,
                                           self.cpp_class.pytypestruct))
            wrapper.after_call.unindent()
        else:
            wrapper_type = '&'+self.cpp_class.pytypestruct

        ## Create the Python wrapper object
        py_name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', 'py_'+self.cpp_class.name)
        wrapper.after_call.write_code(
            "%s = PyObject_New(%s, %s);" %
            (py_name, self.cpp_class.pystruct, wrapper_type))
        
        ## Assign the C++ value to the Python wrapper
        if self.caller_owns_return:
            wrapper.after_call.write_code("%s->obj = %s;" % (py_name, value))
        else:
            if self.cpp_class.incref_method is None:
                ## The PyObject creates its own copy
                wrapper.after_call.write_code(
                    "%s->obj = new %s(*%s);"
                    % (py_name, self.cpp_class.name, value))
            else:
                ## The PyObject gets a new reference to the same obj
                wrapper.after_call.write_code(
                    "%s->%s();" % (value, self.cpp_class.incref_method))
                wrapper.after_call.write_code("%s->obj = %s;" % (py_name, value))
                
        wrapper.build_params.add_parameter("N", [py_name], prepend=True)


    def convert_python_to_c(self, wrapper):
        """See ReturnValue.convert_python_to_c"""
        name = wrapper.declarations.declare_variable(
            self.cpp_class.pystruct+'*', "tmp_%s" % self.cpp_class.name)
        wrapper.parse_params.add_parameter(
            'O!', ['&'+self.cpp_class.pytypestruct, '&'+name])

        value = self.transformation.untransform(
            self, wrapper.declarations, wrapper.after_call, "%s->obj" % name)

        ## now the hairy part :)
        if self.caller_owns_return:
            if self.cpp_class.incref_method is None:
                ## the caller receives a copy
                wrapper.after_call.write_code(
                    "%s = new %s(*%s);"
                    % (self.value, self.cpp_class.name, value))
            else:
                ## the caller gets a new reference to the same obj
                wrapper.after_call.write_code(
                    "%s->%s();" % (value, self.cpp_class.incref_method))
                wrapper.after_call.write_code(
                    "%s = %s;" % (self.value, value))
        else:
            ## caller gets a shared pointer
            ## but this is dangerous, avoid at all cost!!!
            wrapper.after_call.write_code(
                "// dangerous!\n%s = %s;" % (self.value, value))
            warnings.warn("Returning shared pointers is dangerous!"
                          "  The C++ API should be redesigned "
                          "to avoid this situation.")
            
