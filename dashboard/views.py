# -*- coding: utf-8 -*-
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic.base import TemplateView
from django.views.generic.detail import DetailView

from babybuddy.mixins import LoginRequiredMixin, PermissionRequiredMixin
from core import timeline
from core.models import Child


class Dashboard(LoginRequiredMixin, TemplateView):
    # TODO: Use .card-deck in this template once BS4 is finalized.
    template_name = "dashboard/dashboard.html"

    # Show the overall dashboard or a child dashboard if one Child instance.
    def get(self, request, *args, **kwargs):
        children = Child.objects.count()
        if children == 0:
            return HttpResponseRedirect(reverse("babybuddy:welcome"))
        elif children == 1:
            return HttpResponseRedirect(
                reverse("dashboard:dashboard-child", args={Child.objects.first().slug})
            )
        return super(Dashboard, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(Dashboard, self).get_context_data(**kwargs)
        context["objects"] = Child.objects.all().order_by(
            "last_name", "first_name", "id"
        )
        return context


class ChildDashboard(PermissionRequiredMixin, DetailView):
    model = Child
    permission_required = ("core.view_child",)
    template_name = "dashboard/child.html"

    def get_context_data(self, **kwargs):
        context = super(ChildDashboard, self).get_context_data(**kwargs)
        # The dashboard's "Today" panel is the timeline, scoped to today.
        date = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        context["timeline_objects"] = timeline.get_objects(date, self.object)
        context["switch_children"] = Child.objects.exclude(id=self.object.id).order_by(
            "first_name", "last_name", "id"
        )
        return context
