from rest_framework.routers import SimpleRouter

from isik.django.apps.scheduling.drf import ScheduledTaskViewSet


router = SimpleRouter()
router.register("scheduled-tasks", ScheduledTaskViewSet, basename="scheduled-task")
urlpatterns = router.urls
