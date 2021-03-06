from collections import OrderedDict

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template import loader

from capapi import resources
from capapi.forms import RegisterUserForm, ResendVerificationForm, ResearchRequestForm
from capapi.models import SiteLimits, CapUser
from capapi.resources import form_for_request
from capdb.models import CaseExport
from capweb.helpers import reverse


def register_user(request):
    """ Create new user """
    form = form_for_request(request, RegisterUserForm)

    if request.method == 'POST' and form.is_valid():
        form.save()
        resources.send_new_signup_email(request, form.instance)
        return render(request, 'registration/sign-up-success.html', {
            'status': 'Success!',
            'message': 'Thank you. Please check your email for a verification link.',
            'page_name': 'user-register-success'
        })

    return render(request, 'registration/register.html', {'form': form,
                                                          'page_name': 'user-register'})


def verify_user(request, user_id, activation_nonce):
    """ Verify email and assign api token """
    try:
        user = CapUser.objects.get(pk=user_id)
        user.authenticate_user(activation_nonce=activation_nonce)
    except (CapUser.DoesNotExist, PermissionDenied):
        error = "Unknown verification code."
    else:
        # user authenticated successfully
        error = None

        # update API limits for first 50 users per day
        site_limits = SiteLimits.add_values(daily_signups=1)
        if site_limits.daily_signups < site_limits.daily_signup_limit:
            user.total_case_allowance = user.case_allowance_remaining = settings.API_CASE_DAILY_ALLOWANCE
            user.save()
    return render(request, 'registration/verified.html', {
        'contact_email': settings.DEFAULT_FROM_EMAIL,
        'error': error,
        'page_name': 'user-verify'
    })


def resend_verification(request):
    """ Resend verification code """
    form = form_for_request(request, ResendVerificationForm)

    if request.method == 'POST' and form.is_valid():
        try:
            user = CapUser.objects.get(email=form.cleaned_data['email'])
        except CapUser.DoesNotExist:
            form.add_error('email', "User with that email does not exist.")
        else:
            if user.email_verified:
                form.add_error('email', "Email address is already verified.")
        if form.is_valid():
            resources.send_new_signup_email(request, user)
            return render(request, 'registration/sign-up-success.html', {
                'status': 'Success!',
                'message': 'Thank you. Please check your email %s for a verification link.' % user.email
            })
    return render(request, 'registration/resend-nonce.html', {
        'info_email': settings.DEFAULT_FROM_EMAIL,
        'form': form,
        'page_name': 'user-resend-verification'
    })


@login_required
def user_details(request):
    """ Show user details """
    request.user.update_case_allowance()
    context = {
        'unlimited': request.user.unlimited_access_in_effect(),
        'page_name': 'user-details'
    }
    return render(request, 'registration/user-details.html', context)


@login_required
def request_research_access(request):
    name = "%s %s" % (request.user.first_name, request.user.last_name)
    form = form_for_request(request, ResearchRequestForm, initial={'name': name, 'email': request.user.email})

    if request.method == 'POST' and form.is_valid():
        form.instance.user = request.user
        form.save()
        message = loader.get_template('research_request/research_request_email.txt').render(form.cleaned_data)
        send_mail(
            'Research access requested',
            message,
            settings.DEFAULT_FROM_EMAIL,
            [settings.DEFAULT_FROM_EMAIL],
            fail_silently=False)
        return HttpResponseRedirect(reverse('research-request-success'))

    return render(request, 'research_request/research_request.html', {'form': form})


def bulk(request):
    """ List zips available for download """
    query = CaseExport.objects.exclude_old().order_by('body_format')

    # only show private downloads to logged in users
    if not request.user.unlimited_access_in_effect():
        query = query.filter(public=True)

    # sort exports by filter_type, filter_item, body_format so various levels are consistently sorted
    exports = list(query)
    CaseExport.load_filter_items(exports)
    exports.sort(key=lambda x: (x.filter_type, str(x.filter_item), x.body_format))

    # group exports into the hierarchy they'll appear on the page, making a dictionary like:
    # sorted_exports = {
    #   'public': {
    #       'jurisdiction': {
    #           <Jurisdiction>: {'xml': <CaseExport>, 'text': <CaseExport>},
    #       },
    #       'reporter': <ditto>
    #   'private: { <ditto> }
    # }
    sorted_exports = {}
    for export in exports:
        sorted_exports\
            .setdefault('public' if export.public else 'private', OrderedDict())\
            .setdefault(export.filter_type, OrderedDict())\
            .setdefault(export.filter_item, OrderedDict()) \
            [export.body_format] = export

    return render(request, 'bulk.html', {
        'exports': sorted_exports,
    })