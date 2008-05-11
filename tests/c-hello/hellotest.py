import sys
import gc
import os.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'build', 'default', 'tests', 'c-hello'))
import hello
import unittest


class TestHello(unittest.TestCase):

    def test_func(self):
        x = hello.sum(1, 2)
        self.assertEqual(x, 3)

    def test_foo(self):
        foo = hello.Foo()
        data = foo.get_data()
        self.assertEqual(data, None)
        del foo
        while gc.collect():
            pass

    def test_foo2(self):
        foo = hello.Foo("123")
        data = foo.get_data()
        self.assertEqual(data, "123")
        del foo
        while gc.collect():
            pass

    def test_foo3(self):
        foo = hello.Foo(3)
        data = foo.get_data()
        self.assertEqual(data, "   ")
        del foo
        while gc.collect():
            pass


if __name__ == '__main__':
    unittest.main()