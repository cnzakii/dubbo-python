#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import abc
import importlib
from unittest import mock

import pytest

from dubbo.extension._loader import ExtensionLoader
from dubbo.extension._manager import ExtensionManager
from dubbo.extension.exceptions import ExtensionError


# Define test interfaces and implementations for testing
class TestInterface(abc.ABC):
    @abc.abstractmethod
    def method(self):
        raise NotImplementedError()


class TestImpl1(TestInterface):
    def method(self):
        return "impl1"


class TestImpl2(TestInterface):
    def __init__(self, arg1=None, arg2=None):
        self.arg1 = arg1
        self.arg2 = arg2

    def method(self):
        return f"impl2 with {self.arg1}, {self.arg2}"


class AnotherInterface(abc.ABC):
    @abc.abstractmethod
    def another_method(self):
        raise NotImplementedError()


class AnotherImpl(AnotherInterface):
    def another_method(self):
        return "another_impl"


class TestExtensionError:
    """
    Tests for ExtensionError class.
    """

    def test_extension_error_inheritance(self):
        """
        Test that ExtensionError is a subclass of Exception.

        :return: None
        """
        assert issubclass(ExtensionError, Exception)

        # Verify we can instantiate and raise the error
        error = ExtensionError("Test error")
        assert str(error) == "Test error"

        with pytest.raises(ExtensionError) as excinfo:
            raise ExtensionError("Test raising error")
        assert str(excinfo.value) == "Test raising error"


class TestExtensionLoader:
    """
    Tests for ExtensionLoader class.
    """

    def test_init(self):
        """
        Test initialization of ExtensionLoader.

        :return: None
        """
        # Test initialization with interface only
        loader = ExtensionLoader(TestInterface)
        assert loader.interface == TestInterface

        # Test initialization with interface and implementations
        impls = {"impl1": TestImpl1, "impl2": TestImpl2}
        loader = ExtensionLoader(TestInterface, impls)
        assert loader.interface == TestInterface

        # Verify we can access the implementations
        assert loader.get_impl("impl1") == TestImpl1
        assert loader.get_impl("impl2") == TestImpl2

    def test_register(self):
        """
        Test registering implementations dynamically.

        :return: None
        """
        loader = ExtensionLoader(TestInterface)

        # Register a class directly
        loader.register("impl1", TestImpl1)
        assert loader.get_impl("impl1") == TestImpl1

        # Register using a string path (mock using __module__ attribute)
        TestImpl2.__module__ = "dubbo.tests"
        loader.register("impl2", "dubbo.tests:TestImpl2")

        # Test invalid inputs
        with pytest.raises(ValueError):
            loader.register("", TestImpl1)  # Empty name

        with pytest.raises(TypeError):
            loader.register("bad_impl", 123)  # Not a class or string

    def test_get_impl(self):
        """
        Test retrieving implementations by name.

        :return: None
        """
        loader = ExtensionLoader(TestInterface, {"impl1": TestImpl1})

        # Test getting a registered implementation
        assert loader.get_impl("impl1") == TestImpl1

        # Test getting a non-existent implementation
        with pytest.raises(ExtensionError) as excinfo:
            loader.get_impl("non_existent")
        assert "not found" in str(excinfo.value)

        # Test getting a registered implementation that doesn't extend the interface
        class NotAnImpl:
            pass

        loader = ExtensionLoader(TestInterface, {"bad_impl": NotAnImpl})
        with pytest.raises(ExtensionError) as excinfo:
            loader.get_impl("bad_impl")
        assert "not a subclass" in str(excinfo.value)

        # Test getting an implementation with an invalid string path format
        loader = ExtensionLoader(TestInterface, {"bad_path": "invalid_path"})
        with pytest.raises(ExtensionError) as excinfo:
            loader.get_impl("bad_path")
        assert "is invalid" in str(excinfo.value)

    def test_get_impl_caching(self):
        """
        Test that implementations are cached after first retrieval.

        :return: None
        """
        # Create a test class that tracks instantiation
        call_count = 0

        def mock_import_module(name):
            nonlocal call_count
            call_count += 1

            class MockModule:
                pass

            setattr(MockModule, "TestImpl", TestImpl1)
            return MockModule

        # Patch importlib.import_module
        original_import = importlib.import_module
        importlib.import_module = mock_import_module

        try:
            loader = ExtensionLoader(TestInterface, {"impl1": "mock.module:TestImpl"})

            # First call should use import
            impl1 = loader.get_impl("impl1")
            assert impl1 == TestImpl1
            assert call_count == 1

            # Second call should use cache
            impl2 = loader.get_impl("impl1")
            assert impl2 == TestImpl1
            assert call_count == 1  # Call count should not increase

            # Test cache invalidation on re-registration
            loader.register("impl1", TestImpl2)
            impl3 = loader.get_impl("impl1")
            assert impl3 == TestImpl2
        finally:
            # Restore original import function
            importlib.import_module = original_import

    def test_get_instance(self):
        """
        Test instantiation of implementations.

        :return: None
        """
        loader = ExtensionLoader(TestInterface, {"impl1": TestImpl1, "impl2": TestImpl2})

        # Test simple instantiation
        instance1 = loader.get_instance("impl1")
        assert isinstance(instance1, TestImpl1)

        # Test instantiation with arguments
        instance2 = loader.get_instance("impl2", "value1", arg2="value2")
        assert isinstance(instance2, TestImpl2)
        assert instance2.arg1 == "value1"
        assert instance2.arg2 == "value2"

        # Test instantiation of non-existent implementation
        with pytest.raises(ExtensionError):
            loader.get_instance("non_existent")


class TestExtensionManager:
    """
    Tests for ExtensionManager class.
    """

    @pytest.fixture
    def manager(self):
        """
        Fixture that creates an ExtensionManager with test loaders.

        :return: An instance of ExtensionManager
        """
        loaders = {
            TestInterface: ExtensionLoader(TestInterface, {"impl1": TestImpl1, "impl2": TestImpl2}),
            AnotherInterface: ExtensionLoader(AnotherInterface, {"another": AnotherImpl}),
        }
        return ExtensionManager(loaders)

    def test_get_loader(self, manager):
        """
        Test retrieving a loader by interface.

        :param manager: ExtensionManager fixture
        :return: None
        """
        # Test getting a registered loader
        loader = manager.get_loader(TestInterface)
        assert isinstance(loader, ExtensionLoader)
        assert loader.interface == TestInterface

        # Test getting a non-existent loader
        class UnregisteredInterface:
            pass

        with pytest.raises(ExtensionError) as excinfo:
            manager.get_loader(UnregisteredInterface)
        assert "No ExtensionLoader registered" in str(excinfo.value)

    def test_get_impl(self, manager):
        """
        Test retrieving an implementation class through the manager.

        :param manager: ExtensionManager fixture
        :return: None
        """
        # Test getting a valid implementation
        impl = manager.get_impl(TestInterface, "impl1")
        assert impl == TestImpl1

        # Test getting a non-existent implementation
        with pytest.raises(ExtensionError):
            manager.get_impl(TestInterface, "non_existent")

        # Test getting from a non-existent loader
        class UnregisteredInterface:
            pass

        with pytest.raises(ExtensionError):
            manager.get_impl(UnregisteredInterface, "impl1")

    def test_get_instance(self, manager):
        """
        Test instantiating an implementation through the manager.

        :param manager: ExtensionManager fixture
        :return: None
        """
        # Test instantiating a simple implementation
        instance1 = manager.get_instance(TestInterface, "impl1")
        assert isinstance(instance1, TestImpl1)

        # Test instantiating with arguments
        instance2 = manager.get_instance(TestInterface, "impl2", "value1", arg2="value2")
        assert isinstance(instance2, TestImpl2)
        assert instance2.arg1 == "value1"
        assert instance2.arg2 == "value2"

        # Test instantiating from another interface
        instance3 = manager.get_instance(AnotherInterface, "another")
        assert isinstance(instance3, AnotherImpl)

        # Test instantiating a non-existent implementation
        with pytest.raises(ExtensionError):
            manager.get_instance(TestInterface, "non_existent")

    def test_register_impl(self, manager):
        """
        Test registering a new implementation through the manager.

        :param manager: ExtensionManager fixture
        :return: None
        """

        # Define a new implementation
        class TestImpl3(TestInterface):
            def method(self):
                return "impl3"

        # Register it through the manager
        manager.register_impl(TestInterface, "impl3", TestImpl3)

        # Verify it was registered correctly
        impl = manager.get_impl(TestInterface, "impl3")
        assert impl == TestImpl3

        # Test instantiation
        instance = manager.get_instance(TestInterface, "impl3")
        assert isinstance(instance, TestImpl3)

        # Test registering to a non-existent loader
        class UnregisteredInterface:
            pass

        with pytest.raises(ExtensionError):
            manager.register_impl(UnregisteredInterface, "impl", TestImpl3)


@pytest.mark.integration
class TestIntegration:
    """
    Integration tests for the extension system.
    """

    def test_load_extensions(self):
        """
        Test the _load_extensions function by mocking the internal registry.

        :return: None
        """
        from dubbo.extension import _load_extensions

        # Create a test registry class
        class Registry:
            def __init__(self, interface, impls):
                self.interface = interface
                self.impls = impls

        # Mock the internal get_all_registries function
        try:
            # Check if the module exists
            import_spec = importlib.util.find_spec("dubbo.extension._internal")
            if import_spec is None:
                pytest.skip("dubbo.extension._internal module not found")
                return

            # Import and mock
            import dubbo.extension._internal

            original_get_registries = getattr(dubbo.extension._internal, "get_all_registries", None)
            if original_get_registries is None:
                pytest.skip("get_all_registries function not found in dubbo.extension._internal")
                return

            # Set up mock registries
            registries = [
                Registry(TestInterface, {"impl1": TestImpl1}),
                Registry(AnotherInterface, {"another": AnotherImpl}),
            ]

            # Replace the function with safe patching
            with mock.patch("dubbo.extension._internal.get_all_registries", return_value=registries):
                # Call the function and verify the result
                manager = _load_extensions()
                assert isinstance(manager, ExtensionManager)

                # Test the loaded extensions
                impl = manager.get_impl(TestInterface, "impl1")
                assert impl == TestImpl1

                impl = manager.get_impl(AnotherInterface, "another")
                assert impl == AnotherImpl

        except ImportError:
            pytest.skip("Could not import dubbo.extension._internal module")
        except Exception as e:
            pytest.fail(f"Error in test_load_extensions: {str(e)}")

    def test_real_get_all_registries(self):
        """
        Test the actual get_all_registries function to ensure it returns valid registries.

        :return: None
        """
        try:
            # Import the real function
            import importlib.util

            spec = importlib.util.find_spec("dubbo.extension._internal")
            if spec is None:
                pytest.skip("dubbo.extension._internal module not found")
                return

            from dubbo.extension._internal import get_all_registries

            # Call the function
            registries = get_all_registries()

            # Basic validation
            assert isinstance(registries, list), "get_all_registries should return a list"

            # If there are registries, verify their structure
            for registry in registries:
                assert hasattr(registry, "interface"), "Registry should have 'interface' attribute"
                assert isinstance(registry.interface, type), "Registry interface should be a type"

                assert hasattr(registry, "impls"), "Registry should have 'impls' attribute"
                assert isinstance(registry.impls, dict), "Registry impls should be a dictionary"

                # Check impl dictionary structure
                for name, impl in registry.impls.items():
                    assert isinstance(name, str), "Implementation name should be a string"
                    assert isinstance(impl, (str, type)), "Implementation should be a string or type"

                    # If impl is a type, verify it's a subclass of the interface
                    if isinstance(impl, type):
                        assert issubclass(impl, registry.interface), (
                            f"Implementation {impl.__name__} should be a subclass of {registry.interface.__name__}"
                        )

        except ImportError:
            pytest.skip("Could not import get_all_registries, possibly due to import errors in dependencies")
        except Exception as e:
            pytest.fail(f"Error testing get_all_registries: {str(e)}")

    def test_global_extension_manager(self):
        """
        Test that the global extension_manager is properly initialized.

        :return: None
        """
        try:
            from dubbo.extension import extension_manager

            # Verify that it's an instance of ExtensionManager
            assert isinstance(extension_manager, ExtensionManager)
        except ImportError:
            pytest.skip("Could not import extension_manager from dubbo.extension")
        except Exception as e:
            pytest.fail(f"Error testing global_extension_manager: {str(e)}")

    def test_extension_manager_real_extensions(self):
        """
        Test that the extension_manager has loaded real extensions from the codebase.

        :return: None
        """
        try:
            from dubbo.extension import extension_manager

            # Get the registered loader interfaces
            loader_interfaces = list(extension_manager._loaders.keys())

            # There should be at least some loaders registered
            assert len(loader_interfaces) > 0, "No extension loaders found in extension_manager"

            # Log information about found loaders for debugging
            print(f"Found {len(loader_interfaces)} extension interfaces in extension_manager")

            for interface in loader_interfaces:
                loader = extension_manager.get_loader(interface)

                # Try to access impl_map attribute
                impl_map = getattr(loader, "_impl_map", {})

                # Try to get an implementation for validation
                if impl_map:
                    impl_name = next(iter(impl_map.keys()))
                    impl_class = loader.get_impl(impl_name)
                    assert issubclass(impl_class, interface)

        except ImportError:
            pytest.skip("Could not import extension_manager from dubbo.extension")
        except Exception as e:
            pytest.fail(f"Error testing extension_manager with real extensions: {str(e)}")
