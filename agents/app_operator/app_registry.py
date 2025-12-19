"""Application registry for managing available applications."""

from typing import Type

from app_operator.application import Application
from app_operator.apps.hotel import HotelApplication


class ApplicationRegistry:
    """Registry of available applications.
    
    This registry maintains a mapping of application names to their
    implementation classes. It provides methods to retrieve application
    instances and list available applications.
    """
    
    def __init__(self):
        """Initialize the application registry."""
        self._apps: dict[str, Type[Application]] = {
            "hotel": HotelApplication,
        }
    
    def get(self, name: str) -> Application:
        """Get an application instance by name.
        
        Args:
            name: The name of the application to retrieve.
            
        Returns:
            Application: An instance of the requested application.
            
        Raises:
            ValueError: If the application name is not registered.
        """
        if name not in self._apps:
            available = ", ".join(self._apps.keys())
            raise ValueError(
                f"Unknown application: '{name}'. "
                f"Available applications: {available}"
            )
        
        app_class = self._apps[name]
        return app_class()
    
    def list_applications(self) -> list[str]:
        """List all available application names.
        
        Returns:
            list[str]: A list of registered application names.
        """
        return list(self._apps.keys())
    
    def get_descriptions(self) -> dict[str, str]:
        """Get all applications with their descriptions.
        
        Returns:
            dict[str, str]: A mapping of application names to descriptions.
        """
        descriptions = {}
        for name, app_class in self._apps.items():
            try:
                # Instantiate to get description
                app_instance = app_class()
                descriptions[name] = app_instance.description
            except Exception as e:
                descriptions[name] = f"[Error loading description: {e}]"
        
        return descriptions
    
    def register(self, name: str, app_class: Type[Application]) -> None:
        """Register a new application.
        
        This method allows dynamically adding new applications to the registry.
        
        Args:
            name: The name to register the application under.
            app_class: The application class to register.
            
        Raises:
            ValueError: If an application with the same name is already registered.
        """
        if name in self._apps:
            raise ValueError(f"Application '{name}' is already registered")
        
        self._apps[name] = app_class


# Global registry instance
registry = ApplicationRegistry()
