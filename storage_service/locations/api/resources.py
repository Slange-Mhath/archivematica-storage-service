# stdlib, alphabetical
import json
import logging
import mimetypes
import os
import subprocess
import tempfile

# Core Django, alphabetical
from django.conf.urls import url
from django.core.servers.basehttp import FileWrapper
from django.forms.models import model_to_dict

# Third party dependencies, alphabetical
from tastypie.authentication import (BasicAuthentication, ApiKeyAuthentication,
    MultiAuthentication, Authentication)
from tastypie.authorization import DjangoAuthorization, Authorization
from tastypie import fields
from tastypie import http
from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from tastypie.validation import CleanedDataFormValidation
from tastypie.utils import trailing_slash

# This project, alphabetical
from common import utils

from ..models import (Event, Package, Location, Space, Pipeline)
from ..forms import LocationForm, SpaceForm
from ..constants import PROTOCOL

LOGGER = logging.getLogger(__name__)
logging.basicConfig(filename="/tmp/storage_service.log",
    level=logging.INFO)

# FIXME ModelResources with ForeignKeys to another model don't work with
# validation = CleanedDataFormValidation  On creation, it errors with:
# "Select a valid choice. That choice is not one of the available choices."
# This is because the ModelResource accepts a URI, but does not convert it to a
# primary key (in our case, UUID) before passing it to Django.
# See https://github.com/toastdriven/django-tastypie/issues/152 for details


class PipelineResource(ModelResource):
    # Attributes used for POST, exclude from GET
    create_default_locations = fields.BooleanField(use_in=lambda x: False)
    shared_path = fields.CharField(use_in=lambda x: False)

    class Meta:
        queryset = Pipeline.active.all()
        authentication = Authentication()
        # authentication = MultiAuthentication(
        #     BasicAuthentication, ApiKeyAuthentication())
        authorization = Authorization()
        # authorization = DjangoAuthorization()
        # validation = CleanedDataFormValidation(form_class=PipelineForm)

        fields = ['uuid', 'description']
        list_allowed_methods = ['get', 'post']
        detail_allowed_methods = ['get']
        detail_uri_name = 'uuid'
        always_return_data = True
        filtering = {
            'description': ALL,
            'uuid': ALL,
        }

    def obj_create(self, bundle, **kwargs):
        bundle = super(PipelineResource, self).obj_create(bundle, **kwargs)
        bundle.obj.enabled = not utils.get_setting('pipelines_disabled', False)
        create_default_locations = bundle.data.get('create_default_locations', False)
        shared_path = bundle.data.get('shared_path', None)
        bundle.obj.save(create_default_locations, shared_path)
        return bundle


class SpaceResource(ModelResource):
    class Meta:
        queryset = Space.objects.all()
        authentication = Authentication()
        # authentication = MultiAuthentication(
        #     BasicAuthentication, ApiKeyAuthentication())
        authorization = Authorization()
        # authorization = DjangoAuthorization()
        validation = CleanedDataFormValidation(form_class=SpaceForm)

        fields = ['access_protocol', 'last_verified', 'location_set', 'path',
            'size', 'used', 'uuid', 'verified']
        list_allowed_methods = ['get']
        detail_allowed_methods = ['get']
        detail_uri_name = 'uuid'
        always_return_data = True
        filtering = {
            'access_protocol': ALL,
            'path': ALL,
            'size': ALL,
            'used': ALL,
            'uuid': ALL,
            'verified': ALL,
        }

    def prepend_urls(self):
        return [
            url(r"^(?P<resource_name>%s)/(?P<%s>\w[\w/-]*)/browse%s$" % (self._meta.resource_name, self._meta.detail_uri_name, trailing_slash()), self.wrap_view('browse'), name="browse"),
        ]

    # Is there a better place to add protocol-specific space info?
    # alter_detail_data_to_serialize
    # alter_deserialized_detail_data

    def dehydrate(self, bundle):
        """ Add protocol specific fields to an entry. """
        bundle = super(SpaceResource, self).dehydrate(bundle)
        access_protocol = bundle.obj.access_protocol
        model = PROTOCOL[access_protocol]['model']

        try:
            space = model.objects.get(space=bundle.obj.uuid)
        except model.DoesNotExist:
            print "Item doesn't exist :("
            # TODO this should assert later once creation/deletion stuff works
        else:
            keep_fields = PROTOCOL[access_protocol]['fields']
            added_fields = model_to_dict(space, keep_fields)
            bundle.data.update(added_fields)

        return bundle

    def obj_create(self, bundle, **kwargs):
        """ Creates protocol specific class when creating a Space. """
        # TODO How to move this to the model?
        # Make dict of fields in model and values from bundle.data
        access_protocol = bundle.data['access_protocol']
        keep_fields = PROTOCOL[access_protocol]['fields']
        fields_dict = { key: bundle.data[key] for key in keep_fields }

        bundle = super(SpaceResource, self).obj_create(bundle, **kwargs)

        model = PROTOCOL[access_protocol]['model']
        obj = model.objects.create(space=bundle.obj, **fields_dict)
        obj.save()
        return bundle

    def browse(self, request, **kwargs):
        """ Returns all of the entries in a space, optionally at a subpath.

        Returns a dict with
            {'entries': [list of entries in the directory],
             'directories': [list of directories in the directory]}
        Directories is a subset of entries, all are just the name.

        If a path=<path> parameter is provided, will look in that path inside
        the Space. """
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.throttle_check(request)
        path = request.GET.get('path', '')
        space = Space.objects.get(uuid=kwargs['uuid'])
        path = os.path.join(space.path, path)

        objects = space.browse(path)

        self.log_throttled_access(request)
        return self.create_response(request, objects)


class LocationResource(ModelResource):
    space = fields.ForeignKey(SpaceResource, 'space')
    path = fields.CharField(attribute='full_path', readonly=True)
    description = fields.CharField(attribute='get_description', readonly=True)
    pipeline = fields.ToManyField(PipelineResource, 'pipeline')

    class Meta:
        queryset = Location.active.all()
        authentication = Authentication()
        # authentication = MultiAuthentication(
        #     BasicAuthentication, ApiKeyAuthentication())
        authorization = Authorization()
        # authorization = DjangoAuthorization()
        # validation = CleanedDataFormValidation(form_class=LocationForm)

        fields = ['enabled', 'relative_path', 'purpose', 'quota', 'used', 'uuid']
        list_allowed_methods = ['get']
        detail_allowed_methods = ['get']
        detail_uri_name = 'uuid'
        always_return_data = True
        filtering = {
            'relative_path': ALL,
            'pipeline': ALL_WITH_RELATIONS,
            'purpose': ALL,
            'quota': ALL,
            'space': ALL_WITH_RELATIONS,
            'used': ALL,
            'uuid': ALL,
        }

    def prepend_urls(self):
        return [
            url(r"^(?P<resource_name>%s)/(?P<%s>\w[\w/-]*)/browse%s$" % (self._meta.resource_name, self._meta.detail_uri_name, trailing_slash()), self.wrap_view('browse'), name="browse"),
        ]

    def browse(self, request, **kwargs):
        """ Returns all of the entries in a location, optionally at a subpath.

        Returns a dict with
            {'entries': [list of entries in the directory],
             'directories': [list of directories in the directory]}
        Directories is a subset of entries, all are just the name.

        If a path=<path> parameter is provided, will look in that path inside
        the Location. """
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.throttle_check(request)
        path = request.GET.get('path', '')
        location = Location.objects.get(uuid=kwargs['uuid'])
        path = os.path.join(location.full_path(), path)

        objects = location.space.browse(path)

        self.log_throttled_access(request)
        return self.create_response(request, objects)


class PackageResource(ModelResource):
    """ Resource for managing Packages.

    List (api/v1/file/) supports:
    GET: List of files
    POST: Create new Package

    Detail (api/v1/file/<uuid>/) supports:
    GET: Get details on a specific file

    Download package (/api/v1/file/<uuid>/download/) supports:
    GET: Get package as download

    Extract file (/api/v1/file/<uuid>/extract_file/) supports:
    GET: Extract file from package (param "relative_path_to_file" specifies which file)

    api/v1/file/<uuid>/delete_aip/ supports:
    POST: Create a delete request for that AIP.
    """
    origin_location = fields.ForeignKey(LocationResource, 'origin_location')
    current_location = fields.ForeignKey(LocationResource, 'current_location')

    origin_full_path = fields.CharField(attribute='full_origin_path',
        readonly=True)
    current_full_path = fields.CharField(attribute='full_path', readonly=True)

    class Meta:
        queryset = Package.objects.all()
        resource_name = 'file'
        authentication = Authentication()
        # authentication = MultiAuthentication(
        #     BasicAuthentication, ApiKeyAuthentication())
        authorization = Authorization()
        # authorization = DjangoAuthorization()
        # validation = CleanedDataFormValidation(form_class=PackageForm)

        fields = ['origin_path', 'current_path', 'package_type', 'size', 'status', 'uuid']
        list_allowed_methods = ['get', 'post']
        detail_allowed_methods = ['get']
        detail_uri_name = 'uuid'
        always_return_data = True
        filtering = {
            'location': ALL_WITH_RELATIONS,
            'path': ALL,
            'uuid': ALL,
            'status': ALL
        }

    def prepend_urls(self):
        return [
            url(r"^(?P<resource_name>%s)/(?P<%s>\w[\w/-]*)/delete_aip%s$" % (self._meta.resource_name, self._meta.detail_uri_name, trailing_slash()), self.wrap_view('delete_aip_request'), name="delete_aip_request"),
            url(r"^(?P<resource_name>%s)/(?P<%s>\w[\w/-]*)/extract_file%s$" % (self._meta.resource_name, self._meta.detail_uri_name, trailing_slash()), self.wrap_view('extract_file_request'), name="extract_file_request"),
            url(r"^(?P<resource_name>%s)/(?P<%s>\w[\w/-]*)/download%s$" % (self._meta.resource_name, self._meta.detail_uri_name, trailing_slash()), self.wrap_view('download_request'), name="download_request"),
        ]

    def obj_create(self, bundle, **kwargs):
        bundle = super(PackageResource, self).obj_create(bundle, **kwargs)
        # IDEA add custom endpoints, instead of storing all AIPS that come in?
        if bundle.obj.package_type == Package.AIP:
            bundle.obj.store_aip()
        return bundle

    def delete_aip_request(self, request, **kwargs):
        # Tastypie checks
        self.method_check(request, allowed=['post'])
        self.is_authenticated(request)
        self.throttle_check(request)

        # Load request from body, check it has all the keys we need
        request_info = json.loads(request.body)
        if not all(k in request_info for k in
                ('event_reason', 'pipeline', 'user_id', 'user_email')):
            # Don't have enough information to make the request - return error
            return http.HttpBadRequest

        # Create the Event for file deletion request
        file = Package.objects.get(uuid=kwargs['uuid'])
        if file.package_type != Package.AIP:
            # Can only request deletion on AIPs
            return http.HttpMethodNotAllowed()

        pipeline = Pipeline.objects.get(uuid=request_info['pipeline'])

        # See if an event already exists
        existing_requests = Event.objects.filter(package=file,
            event_type=Event.DELETE, status=Event.SUBMITTED).count()
        if existing_requests < 1:
            delete_request = Event(package=file, event_type=Event.DELETE,
                status=Event.SUBMITTED, event_reason=request_info['event_reason'],
                pipeline=pipeline, user_id=request_info['user_id'],
                user_email=request_info['user_email'], store_data=file.status)
            delete_request.save()

            # Update file status
            file.status = Package.DEL_REQ
            file.save()

            response = {
                'message': 'Delete request created successfully.'
            }

            response_json = json.dumps(response)
            status_code = 202
        else:
            response = {
                'error_message': 'A deletion request already exists for this AIP.'
            }
            status_code = 200

        self.log_throttled_access(request)
        response_json = json.dumps(response)
        return http.HttpResponse(status=status_code, content=response_json,
            mimetype='application/json')

    def extract_file_request(self, request, **kwargs):
        relative_path_to_file = request.GET.get('relative_path_to_file')

        # Tastypie checks
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.throttle_check(request)

        # Get AIP details
        package = Package.objects.get(uuid=kwargs['uuid'])
        if package.package_type != Package.AIP:
            # Can only extract files from AIPs
            return http.HttpMethodNotAllowed()

        # create temp dir to extract to
        temp_dir = tempfile.mkdtemp()

        filename, file_extension = os.path.splitext(package.full_path())

        # extract file from AIP
        if file_extension == '.bz2':
            command_data = [
                'tar',
                'xvjf',
                package.full_path(),
                '-C' + temp_dir,
                relative_path_to_file
            ]
        else:
            command_data = [
                '7za',
                'e',
                '-o' + temp_dir,
                package.full_path(),
                relative_path_to_file
            ]

        subprocess.call(command_data)

        # send extracted file
        if file_extension == '.bz2':
            extracted_file_path = os.path.join(temp_dir, relative_path_to_file)
        else:
            extracted_file_path = os.path.join(temp_dir, os.path.basename(relative_path_to_file))

        # handle 404s
        if not os.path.exists(extracted_file_path):
            return http.HttpResponse('404')

        filename = os.path.basename(extracted_file_path)
        extension = os.path.splitext(extracted_file_path)[1].lower()

        wrapper = FileWrapper(file(extracted_file_path))
        response = http.HttpResponse(wrapper)

        # force download for certain filetypes
        extensions_to_download = ['.7z', '.zip']

        if extension in extensions_to_download:
            response['Content-Type'] = 'application/force-download'
            response['Content-Disposition'] = 'attachment; filename="' + filename + '"'
        else:
            mimetype = mimetypes.guess_type(filename)[0]
            response['Content-type'] = mimetype

        response['Content-Length'] = os.path.getsize(extracted_file_path)

        self.log_throttled_access(request)

        return response

    def download_request(self, request, **kwargs):
        # Tastypie checks
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.throttle_check(request)

        # Get AIP details
        package = Package.objects.get(uuid=kwargs['uuid'])
        if package.package_type != Package.AIP:
            # Can only extract files from AIPs
            return http.HttpMethodNotAllowed()

        filename = os.path.basename(package.full_path())
        extension = os.path.splitext(package.full_path())[1].lower()

        wrapper = FileWrapper(file(package.full_path()))
        response = http.HttpResponse(wrapper)

        # force download for certain filetypes
        extensions_to_download = ['.7z', '.zip']

        if extension in extensions_to_download:
            response['Content-Type'] = 'application/force-download'
            response['Content-Disposition'] = 'attachment; filename="' + filename + '"'
        else:
            mimetype = mimetypes.guess_type(filename)[0]
            response['Content-type'] = mimetype

        response['Content-Length'] = os.path.getsize(package.full_path())

        self.log_throttled_access(request)

        return response
