from annoying.decorators import render_to
from annoying.functions import get_object_or_None
from blogs.views import blog_list
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.paginator import Paginator, InvalidPage
from django.db.models import Count
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.template import RequestContext
from django.views.decorators.cache import never_cache
from haystack.forms import ModelSearchForm
from haystack.query import EmptySearchQuerySet, SearchQuerySet
from pygments.lexers import get_lexer_by_name
from snipts.models import Favorite, Snipt, SniptSecureView
from taggit.models import Tag
from teams.models import Team

import os


RESULTS_PER_PAGE = getattr(settings, 'HAYSTACK_SEARCH_RESULTS_PER_PAGE', 20)


@render_to('snipts/detail.html')
def detail(request, username, snipt_slug):

    snipt = get_object_or_404(Snipt, user__username=username, slug=snipt_slug)
    user = snipt.user

    if snipt.lexer != 'markdown':
        if 'linenos' not in snipt.stylized:
            snipt.save()

    if user != request.user:
        if not snipt.public:
            if 'key' not in request.GET:
                raise Http404
            else:
                if request.GET.get('key') != snipt.key:
                    raise Http404

                if snipt.secure and not request.user.is_authenticated():
                    raise Http404

        snipt.views = snipt.views + 1
        snipt.save()

    if snipt.secure:
        secure_view = SniptSecureView(user=request.user, snipt=snipt)
        secure_view.save()

    tags = Tag.objects

    if user == request.user:
        tags = tags.filter(snipt__user=user)
        public = False
    else:
        tags = tags.filter(snipt__user=user, snipt__public=True)
        public = True

    tags = tags.annotate(count=Count('taggit_taggeditem_items__id'))
    tags = tags.order_by('-count', 'name')

    return {
        'detail': True,
        'has_snipts': True,
        'public': public,
        'snipt': snipt,
        'tags': tags,
        'user': user,
    }


def download(request, snipt_key):
    snipt = get_object_or_404(Snipt, key=snipt_key)
    return HttpResponse(snipt.code, content_type='application/x-download')


def embed(request, snipt_key):
    snipt = get_object_or_404(Snipt, key=snipt_key)

    lines = snipt.embedded.split('\n')
    return render(request,
                  'snipts/embed.html',
                  {'lines': lines, 'snipt': snipt},
                  content_type='application/javascript')


@render_to('snipts/list-user.html')
def blog_posts(request, username):

    if request.blog_user:
        raise Http404

    if request.user.username == username:
        public = False
        public_user = False
        user = request.user
        snipts = Snipt.objects.filter(user=request.user, blog_post=True)
        tags = Tag.objects.filter(snipt__user=request.user).distinct()
    else:
        public = True
        public_user = True
        user = get_object_or_404(User, username=username)
        snipts = Snipt.objects.filter(blog_post=True, user=user, public=True)
        tags = Tag.objects.filter(snipt__user=user,
                                  snipt__public=True).distinct()

    tags = tags.order_by('name')
    snipts = snipts.order_by('-created')

    context = {
        'has_snipts': True,
        'public': public,
        'public_user': public_user,
        'snipts': snipts,
        'tags': tags,
        'user': user,
    }

    if 'rss' in request.GET:
        context['snipts'] = context['snipts'][:20]
        return rss(request, context)

    return context


@render_to('snipts/list-user.html')
def favorites(request, username):

    if request.user.username != username:
        raise Http404

    if request.blog_user:
        raise Http404

    public = False

    favorites = Favorite.objects.filter(user=request.user).values('snipt')
    favorites = [f['snipt'] for f in favorites]
    snipts = Snipt.objects.filter(Q(pk__in=favorites))

    tags = Tag.objects.filter(snipt__user=request.user).distinct()

    tags = tags.order_by('name')
    snipts = snipts.order_by('-created')

    context = {
        'favorites': favorites,
        'has_snipts': True,
        'public': public,
        'public_user': False,
        'snipts': snipts,
        'tags': tags,
        'user': request.user,
    }

    if 'rss' in request.GET:
        context['snipts'] = context['snipts'][:20]
        return rss(request, context)

    return context


@render_to('snipts/list-public.html')
def list_public(request, tag_slug=None):

    if request.blog_user:
        return blog_list(request)

    snipts = Snipt.objects.filter(public=True).order_by('-created')

    if tag_slug:
        snipts = snipts.filter(tags__slug__in=[tag_slug])
        tag = get_object_or_404(Tag, slug=tag_slug)
    else:
        tag = None

    context = {
        'has_snipts': True,
        'public': True,
        'snipts': snipts,
        'tag': tag,
    }

    if 'rss' in request.GET:
        context['snipts'] = context['snipts'][:20]
        return rss(request, context)

    return context


@render_to('snipts/list-user.html')
def list_user(request, username_or_custom_slug, tag_slug=None):

    if request.blog_user:
        return blog_list(request, username_or_custom_slug)

    user = get_object_or_None(User, username=username_or_custom_slug)

    if user is None:
        snipt = get_object_or_404(Snipt, custom_slug=username_or_custom_slug)
        return detail(request, snipt.user, snipt.slug)

    tags = Tag.objects
    snipts = Snipt.objects

    if user == request.user or \
            (request.GET.get('api_key') == user.api_key.key) or \
            (user.profile.is_a_team and
                user.team.user_is_member(request.user)):

        public = False

        favorites = Favorite.objects.filter(user=user).values('snipt')
        favorites = [f['snipt'] for f in favorites]
        snipts = snipts.filter(Q(user=user) | Q(pk__in=favorites))

        tags = tags.filter(snipt__user=user).distinct()

    else:
        tags = tags.filter(snipt__user=user, snipt__public=True).distinct()
        snipts = snipts.filter(user=user, public=True)
        public = True

    tags = tags.order_by('name')
    snipts = snipts.order_by('-created')

    if tag_slug:
        snipts = snipts.filter(tags__slug__in=[tag_slug])
        tag = get_object_or_404(Tag, slug=tag_slug)
    else:
        tag = None

    if tag is None:
        snipts = snipts.exclude(tags__name__in=['tmp'])

    context = {
        'has_snipts': True,
        'public': public,
        'public_user': (public and user),
        'snipts': snipts,
        'tags': tags,
        'tag': tag,
        'user': user,
        'users_for_full_page': ['robertbanh'],
    }

    if 'rss' in request.GET:
        context['snipts'] = context['snipts'][:20]
        return rss(request, context)

    return context


def raw(request, snipt_key, lexer=None):
    snipt = get_object_or_404(Snipt, key=snipt_key)

    if request.user == snipt.user:
        if lexer:
            lexer = lexer.strip('/')

            if lexer != snipt.lexer:

                try:
                    lexer_obj = get_lexer_by_name(lexer)
                except:
                    lexer_obj = None

                if lexer_obj:
                    snipt.lexer = lexer
                    snipt.save()

    content_type = 'text/plain'

    if 'nice' in request.GET:
        content_type = 'text/html'

    return render(request,
                  'snipts/raw.html',
                  {'snipt': snipt},
                  content_type=content_type)


def rss(request, context):
    return render(request,
                  'rss.xml',
                  context,
                  content_type="application/rss+xml")


@never_cache
def search(request, template='search/search.html', load_all=True,
           form_class=ModelSearchForm, searchqueryset=None,
           context_class=RequestContext, extra_context=None,
           results_per_page=None):

    query = ''
    results = EmptySearchQuerySet()

    if request.GET.get('q'):

        searchqueryset = SearchQuerySet() \
            .filter(Q(public=True) | Q(author=request.user)) \
            .order_by('-pub_date')

        if request.user.is_authenticated() and \
                'mine-only' in request.GET:
            searchqueryset = SearchQuerySet().filter(author=request.user) \
                .order_by('-pub_date')

        elif request.user.is_authenticated() and \
                ('author' in request.GET and
                    request.GET.get('author')):

            author = request.GET.get('author')

            if author == request.user.username:
                searchqueryset = SearchQuerySet().filter(author=request.user) \
                    .order_by('-pub_date')

            else:
                team = get_object_or_None(Team, slug=author)

                if team and team.user_is_member(request.user):
                    searchqueryset = SearchQuerySet().filter(author=team) \
                        .order_by('-pub_date')

        form = ModelSearchForm(request.GET,
                               searchqueryset=searchqueryset,
                               load_all=load_all)

        if form.is_valid():
            query = form.cleaned_data['q']
            results = form.search()
    else:
        form = form_class(searchqueryset=searchqueryset, load_all=load_all)

    paginator = Paginator(results, results_per_page or RESULTS_PER_PAGE)

    try:
        page = paginator.page(int(request.GET.get('page', 1)))
    except InvalidPage:
        raise Http404("No such page of results!")

    context = {
        'form': form,
        'has_snipts': True,
        'page': page,
        'paginator': paginator,
        'query': query,
        'suggestion': None,
    }

    if results.query.backend.include_spelling:
        context['suggestion'] = form.get_suggestion()

    if extra_context:
        context.update(extra_context)

    return render(request, template, context)


def redirect_snipt(request, snipt_key, lexer=None):
    snipt = get_object_or_404(Snipt, key=snipt_key)
    return HttpResponseRedirect(snipt.get_absolute_url())


def redirect_public_tag_feed(request, tag_slug):
    return HttpResponseRedirect('/public/tag/{}/?rss'.format(tag_slug))


def redirect_user_feed(request, username):
    user = get_object_or_404(User, username=username)
    return HttpResponseRedirect(user.get_absolute_url() + '?rss')


def redirect_user_tag_feed(request, username, tag_slug):
    return HttpResponseRedirect(u'/{}/tag/{}/?rss'.format(username, tag_slug))
