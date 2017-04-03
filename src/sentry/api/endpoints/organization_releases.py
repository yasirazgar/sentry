from __future__ import absolute_import

from django.db import IntegrityError, transaction

from rest_framework.response import Response

from .project_releases import ReleaseSerializer
from sentry.api.base import DocSection
from sentry.api.bases.organization import OrganizationReleasesBaseEndpoint
from sentry.api.paginator import OffsetPaginator
from sentry.api.serializers import serialize
from sentry.api.serializers.rest_framework import ReleaseHeadCommitSerializer, ListField
from sentry.models import Activity, Release
from sentry.utils.apidocs import scenario, attach_scenarios


@scenario('CreateNewOrganizationRelease')
def create_new_org_release_scenario(runner):
    runner.request(
        method='POST',
        path='/organizations/%s/releases/' % (runner.org.slug,),
        data={
            'version': '2.0rc2',
            'ref': '6ba09a7c53235ee8a8fa5ee4c1ca8ca886e7fdbb',
            'projects': [runner.default_project.slug],
        }
    )


@scenario('ListOrganizationReleases')
def list_org_releases_scenario(runner):
    runner.request(
        method='GET',
        path='/organizations/%s/releases/' % (runner.org.slug,)
    )


class ReleaseSerializerWithProjects(ReleaseSerializer):
    projects = ListField()
    headCommits = ListField(child=ReleaseHeadCommitSerializer(), required=False)


class OrganizationReleasesEndpoint(OrganizationReleasesBaseEndpoint):
    doc_section = DocSection.RELEASES

    @attach_scenarios([list_org_releases_scenario])
    def get(self, request, organization):
        """
        List an Organization's Releases
        ```````````````````````````````
        Return a list of releases for a given organization.

        :pparam string organization_slug: the organization short name
        :qparam string query: this parameter can be used to create a
                              "starts with" filter for the version.
        """
        query = request.GET.get('query')

        queryset = Release.objects.filter(
            organization=organization,
            projects__in=self.get_allowed_projects(request, organization)
        ).select_related('owner')

        if query:
            queryset = queryset.filter(
                version__istartswith=query,
            )

        queryset = queryset.extra(select={
            'sort': 'COALESCE(date_released, date_added)',
        })

        return self.paginate(
            request=request,
            queryset=queryset,
            order_by='-sort',
            paginator_cls=OffsetPaginator,
            on_results=lambda x: serialize(x, request.user),
        )

    @attach_scenarios([create_new_org_release_scenario])
    def post(self, request, organization):
        """
        Create a New Release for an Organization
        ````````````````````````````````````````
        Create a new release for the given Organization.  Releases are used by
        Sentry to improve its error reporting abilities by correlating
        first seen events with the release that might have introduced the
        problem.
        Releases are also necessary for sourcemaps and other debug features
        that require manual upload for functioning well.

        :pparam string organization_slug: the slug of the organization the
                                          release belongs to.
        :param string version: a version identifier for this release.  Can
                               be a version number, a commit hash etc.
        :param string ref: an optional commit reference.  This is useful if
                           a tagged version has been provided.
        :param url url: a URL that points to the release.  This can be the
                        path to an online interface to the sourcecode
                        for instance.
        :param array projects: a list of project slugs that are involved in
                               this release
        :param datetime dateStarted: an optional date that indicates when the
                                     release process started.
        :param datetime dateReleased: an optional date that indicates when
                                      the release went live.  If not provided
                                      the current time is assumed.
        :auth: required
        """
        serializer = ReleaseSerializerWithProjects(data=request.DATA)

        if serializer.is_valid():
            result = serializer.object

            allowed_projects = {
                p.slug: p for p in self.get_allowed_projects(request, organization)
            }

            projects = []
            for slug in result['projects']:
                if slug not in allowed_projects:
                    return Response({'projects': ['Invalid project slugs']}, status=400)
                projects.append(allowed_projects[slug])

            # release creation is idempotent to simplify user
            # experiences
            try:
                with transaction.atomic():
                    release, created = Release.objects.create(
                        organization_id=organization.id,
                        version=result['version'],
                        ref=result.get('ref'),
                        url=result.get('url'),
                        owner=result.get('owner'),
                        date_started=result.get('dateStarted'),
                        date_released=result.get('dateReleased'),
                    ), True
            except IntegrityError:
                release, created = Release.objects.get(
                    organization_id=organization.id,
                    version=result['version'],
                ), False

            new_projects = []
            for project in projects:
                created = release.add_project(project)
                if created:
                    new_projects.append(project)

            if release.date_released:
                for project in new_projects:
                    Activity.objects.create(
                        type=Activity.RELEASE,
                        project=project,
                        ident=result['version'],
                        data={'version': result['version']},
                        datetime=release.date_released,
                    )

            commit_list = result.get('commits')
            if commit_list:
                release.set_commits(commit_list)

            head_commits = result.get('headCommits')
            if head_commits:
                fetch_commits = request.user.is_authenticated() and not commit_list
                release.set_head_commits(head_commits, request.user, fetch_commits=fetch_commits)

            if not created and not new_projects:
                # This is the closest status code that makes sense, and we want
                # a unique 2xx response code so people can understand when
                # behavior differs.
                #   208 Already Reported (WebDAV; RFC 5842)
                status = 208
            else:
                status = 201

            return Response(serialize(release, request.user), status=status)
        return Response(serializer.errors, status=400)