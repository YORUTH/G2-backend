from rest_framework import generics, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from accounts.permissions import IsManager,IsTrainer
from accounts.models import User
from .serializers import StaffSerializer
from rest_framework import status
from .models import TrainerDietplan
from .serializers import TrainerDietplanSerializer

class TrainerDietplanView(generics.CreateAPIView):
    queryset = TrainerDietplan.objects.all()
    serializer_class = TrainerDietplanSerializer
    permission_classes = [IsTrainer]

class StaffCreateView(generics.CreateAPIView):
    queryset = User.objects.exclude(role="member")  
    serializer_class = StaffSerializer
    permission_classes = [IsManager]

    def perform_create(self, serializer):
        role = serializer.validated_data.get("role")

        if role == "member" or role == "manager":
            raise ValueError("You cannot create members or managers.")

        serializer.save()
class StaffListView(generics.ListAPIView):
    queryset = User.objects.exclude(role__in=["member", "manager"])
    serializer_class = StaffSerializer
    permission_classes = [IsManager]

class StaffDeleteView(generics.DestroyAPIView):
    queryset = User.objects.all()
    permission_classes = [IsManager]

    def destroy(self, request, *args, **kwargs):
        user = get_object_or_404(User, pk=kwargs["pk"])

        if user.role in ["member", "manager"]:
            return Response(
                {"detail": "You can only delete receptionists or trainers."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user.delete()
        return Response({"detail": "Staff account deleted."}, status=status.HTTP_204_NO_CONTENT)
class TrainerDietplanDeleteView(generics.DestroyAPIView):
    queryset = TrainerDietplan.objects.all()
    serializer_class = TrainerDietplanSerializer
    permission_classes = [IsTrainer]