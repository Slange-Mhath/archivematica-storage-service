from __future__ import absolute_import
import base64

import locations.api.resources as resources

from tastypie import fields
from six.moves import map


class PipelineResource(resources.PipelineResource):
    create_default_locations = fields.BooleanField(use_in=lambda x: False)
    shared_path = fields.CharField(use_in=lambda x: False)


class SpaceResource(resources.SpaceResource):
    def get_objects(self, space, path):
        objects = space.browse(path)
        objects["entries"] = list(map(base64.b64encode, objects["entries"]))
        objects["directories"] = list(map(base64.b64encode, objects["directories"]))

        return objects


class LocationResource(resources.LocationResource):
    space = fields.ForeignKey(SpaceResource, "space")
    path = fields.CharField(attribute="full_path", readonly=True)
    pipeline = fields.ToManyField(PipelineResource, "pipeline")

    def decode_path(self, path):
        return base64.b64decode(path)

    def get_objects(self, space, path):
        objects = space.browse(path)
        objects["entries"] = list(map(base64.b64encode, objects["entries"]))
        objects["directories"] = list(map(base64.b64encode, objects["directories"]))
        objects["properties"] = {
            base64.b64encode(k).decode("utf8"): v
            for k, v in objects.get("properties", {}).items()
        }
        return objects


class PackageResource(resources.PackageResource):
    origin_pipeline = fields.ForeignKey(PipelineResource, "origin_pipeline")
    origin_location = fields.ForeignKey(LocationResource, None, use_in=lambda x: False)
    origin_path = fields.CharField(use_in=lambda x: False)
    current_location = fields.ForeignKey(LocationResource, "current_location")

    current_full_path = fields.CharField(attribute="full_path", readonly=True)


class AsyncResource(resources.AsyncResource):
    pass
