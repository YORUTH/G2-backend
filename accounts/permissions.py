from rest_framework import permissions

class IsManager(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == "manager"

class IsReceptionist(permissions.BasePermission):
    """
    Front-desk operations (finance, pending members, lockers, check-in desk).
    Managers (gym owners) have the same access; receptionists are limited to
    GET, POST, and PUT.
    """

    RECEPTIONIST_METHODS = ["GET", "POST", "PUT"]

    def has_permission(self, request, view):
        if request.method == "OPTIONS":
            return True

        if not request.user.is_authenticated:
            return False

        role = request.user.role

        if role == "manager":
            return True

        return (
            role in ["receptionist", "lockerreceptionist"]
            and request.method in self.RECEPTIONIST_METHODS
        )

class IsTrainer(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == "trainer"

class IsMember(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == "member"


class CanViewMembersList(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ["manager", "receptionist", "trainer"]


class CanViewMemberDetail(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):

        if not request.user.is_authenticated:
            return False
        if request.user.role == "trainer":
            return True  
        if request.user.role == "member" and obj.user == request.user:
            return True  
        return False
class IsManagerOrReceptionist(permissions.BasePermission):
    RECEPTIONIST_METHODS = ["GET", "POST", "PUT"]

    def has_permission(self, request, view):
        if request.method == "OPTIONS":
            return True
        if not request.user.is_authenticated:
            return False
        role = request.user.role
        if role == "manager":
            return True
        return role in ["receptionist", "lockerreceptionist"] and request.method in self.RECEPTIONIST_METHODS