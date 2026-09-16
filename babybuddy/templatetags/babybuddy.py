# -*- coding: utf-8 -*-

from django import template
from django.apps import apps
from django.conf import settings
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.functional import lazy
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import to_locale, get_language, gettext_lazy as _
from django.views.generic.edit import DeletionMixin

from axes.helpers import get_lockout_message
from axes.models import AccessAttempt

from core.models import Child

register = template.Library()
mark_safe_lazy = lazy(mark_safe, str)


@register.simple_tag
def axes_lockout_message():
    return get_lockout_message()


@register.simple_tag(takes_context=True)
def relative_url(context, field_name, value):
    """
    Create a relative URL with an updated field value.

    :param context: current request content.
    :param field_name: the field name to update.
    :param value: the new value for field_name.
    :return: encoded relative url with updated query string.
    """
    url = "?{}={}".format(field_name, value)
    querystring = context["request"].GET.urlencode().split("&")
    filtered_querystring = filter(lambda p: p.split("=")[0] != field_name, querystring)
    encoded_querystring = "&".join(filtered_querystring)
    return "{}&{}".format(url, encoded_querystring)


@register.simple_tag()
def version_string():
    """
    Get Baby Buddy's current version string.

    :return: version string ('n.n.n (commit)').
    """
    config = apps.get_app_config("babybuddy")
    return config.version_string


@register.simple_tag()
def get_current_locale():
    """
    Get the current language's locale code.

    :return: locale code (e.g. 'de', 'fr', etc.).
    """
    return to_locale(get_language())


@register.simple_tag()
def get_child_count():
    return Child.count()


@register.simple_tag()
def get_current_timezone():
    return timezone.get_current_timezone_name()


@register.simple_tag(takes_context=True)
def make_absolute_url(context, url):
    request = context["request"]
    abs_url = request.build_absolute_uri(url)
    return abs_url


@register.simple_tag()
def user_is_locked(user):
    return AccessAttempt.objects.filter(username=user.username).exists()


@register.simple_tag()
def user_is_read_only(user):
    return user.groups.filter(name=settings.BABY_BUDDY["READ_ONLY_GROUP_NAME"]).exists()


@register.simple_tag()
def confirm_delete_text(object):
    return mark_safe_lazy(
        _("Are you sure you want to delete %(name)s?")
        % {
            "name": format_html('<span class="text-info">{}</span>', str(object)),
        }
    )


@register.simple_tag()
def confirm_unlock_text(object):
    return mark_safe_lazy(
        _("Are you sure you want to unlock %(name)s?")
        % {
            "name": format_html('<span class="text-info">{}</span>', str(object)),
        }
    )


@register.simple_tag(takes_context=True)
def delete_url(context, object):
    """
    Get the delete URL for the object of the current update view, if the
    current user is able to use it.

    :param context: current request context.
    :param object: the object being updated.
    :return: delete URL or an empty string if no simple delete is available.
    """
    request = context["request"]
    resolver_match = request.resolver_match
    if not object or not resolver_match:
        return ""
    if not resolver_match.url_name or not resolver_match.url_name.endswith("-update"):
        return ""

    url_name = "{}-delete".format(resolver_match.url_name[: -len("-update")])
    if resolver_match.namespace:
        url_name = "{}:{}".format(resolver_match.namespace, url_name)
    try:
        url = reverse(url_name, kwargs=resolver_match.kwargs)
        view_class = resolve(url).func.view_class
    except (NoReverseMatch, Resolver404, AttributeError):
        return ""

    # Some delete views (e.g. Child) require their own confirmation step and
    # so cannot be POSTed to directly.
    if not issubclass(view_class, DeletionMixin):
        return ""

    permissions = getattr(view_class, "permission_required", ()) or ()
    if isinstance(permissions, str):
        permissions = (permissions,)
    if not request.user.has_perms(permissions):
        return ""

    return url
