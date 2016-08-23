import urllib.request, urllib.error, urllib.parse
import json
import sys

from . import ClientError, VALID_LEVELS
from ..serializers import Util

# Common accept header sent
ACCEPT_HDR="application/vnd.schemaregistry.v1+json, application/vnd.schemaregistry+json, application/json"

class CachedSchemaRegistryClient(object):
    """
    A client that talks to a Schema Registry over HTTP

    See http://confluent.io/docs/current/schema-registry/docs/intro.html

    Errors communicating to the server will result in a ClientError being raised.
    """
    def __init__(self, url):
        """Construct a client by passing in the base URL of the schema registry server"""

        self.url = url.rstrip('/')
        self.id_to_schema = {}

    def _send_request(self, url, method='GET', body=None, headers=None):
        if body:
            body = json.dumps(body).encode('utf-8')
	
        new_req = urllib.request.Request(url, data=body)
        # must be callable
        new_req.get_method = lambda: method
        # set the accept header
        new_req.add_header("Accept",ACCEPT_HDR)
        if body:
            new_req.add_header("Content-Length",str(len(body)))
            new_req.add_header("Content-Type","application/json")
        # add additional headers if present
        if headers:
            for header_name in headers:
                new_req.add_header(header_name, headers[header_name])
        try:
            response = urllib.request.urlopen(new_req)
            # read response
            result = json.loads(response.read().decode('utf-8'))
            # build meta with headers as a dict
            meta = response.info()
            # http code
            code = response.getcode()
            # return result + meta tuple
            return (result, meta, code)
        except urllib.request.HTTPError as e:
            code = e.code
            result = json.loads(e.read().decode('utf-8'))
            message = "HTTP Error (%d) from schema registry: %s %d" % (code,
                                                                       result.get('message'),
                                                                       result.get('error_code'))
            raise ClientError(message, code)
        except ClientError as e:
            raise e
        except:
            msg = "An unexpected error occurred: %s" % (str(sys.exc_info()[1]))
            raise ClientError(msg)

    def _cache_schema(self, schema, schema_id, subject=None, version=None):
        # overwrite, not much performance impact, as shouldn't be happening often
        self.id_to_schema[schema_id] = schema

    def register(self, subject, avro_schema):
        """
        Register a schema with the registry under the given subject
        and receive a schema id.

        avro_schema must be a parsed schema from the python avro library

        """

        url = '/'.join([self.url,'subjects',subject,'versions'])
        body = { 'schema' : json.dumps(avro_schema.to_json()) }
        result,meta,code = self._send_request(url, method='POST', body=body)
        schema_id = result['id']
        self._cache_schema(avro_schema, schema_id, subject)
        return schema_id

    def get_by_id(self, schema_id):
        """Retrieve a parsed avro schema by id or None if not found"""

        if schema_id in self.id_to_schema:
            return self.id_to_schema[schema_id]

        url = '/'.join([self.url,'schemas','ids',str(schema_id)])
        try:
            result,meta,code = self._send_request(url)
        except ClientError as e:
            if e.http_code == 404:
                return None
            else:
                raise e
        else:
            # need to parse the schema
            schema_str = result.get("schema")
            try:
                result = Util.parse_schema_from_string(schema_str)
                # cache it
                self._cache_schema(result, schema_id)
                return result
            except:
                # bad schema - should not happen
                raise ClientError("Received bad schema from registry.")

    def get_latest_schema(self, subject):
        """
        Return the latest 3-tuple of:
        (the schema id, the parsed avro schema, the schema version)
        for a particular subject.

        This call always contacts the registry.

        If the subject is not found, (None,None,None) is returned.
        """
        url = '/'.join([self.url, 'subjects',subject,'versions','latest'])
        try:
            result,meta,code = self._send_request(url)
        except ClientError as e:
            if e.http_code == 404:
                return (None, None, None)
            raise e
        schema_id = result['id']
        version = result['version']
        if schema_id in self.id_to_schema:
            schema = self.id_to_schema[schema_id]
        else:
            try:
                schema = Util.parse_schema_from_string(result['schema'])
            except:
                # bad schema - should not happen
                raise ClientError("Received bad schema from registry.")

        self._cache_schema(schema, schema_id, subject, version)
        return (schema_id, schema, version)


    def get_version(self, subject, avro_schema):
        """
        Get the version of a schema for a given subject.

        Returns -1 if not found.
        """
        # schemas_to_version = self.subject_to_schema_versions.get(subject,{})
        # version = schemas_to_version.get(avro_schema, -1)
        # if version != -1:
        #    return version

        url = '/'.join([self.url, 'subjects', subject])
        body = { 'schema' : json.dumps(avro_schema.to_json()) }
        try:
            result,meta,code = self._send_request(url, method='POST', body=body)
            schema_id = result['id']
            version = result['version']
            self._cache_schema(avro_schema, schema_id, subject, version)
            return version
        except ClientError as e:
            if e.http_code == 404:
                return -1
            else:
                raise e

    def test_compatibility(self, subject, avro_schema, version='latest'):
        """
        Test the compatibility of a candidate parsed schema for a given subject.

        By default the latest version is checked against.
        """
        url = '/'.join([self.url,'compatibility','subjects',subject,
                        'versions',str(version)])
        body = { 'schema' : json.dumps(avro_schema.to_json()) }
        try:
            result,meta,code = self._send_request(url, method='POST', body=body)
            return result.get('is_compatible')
        except:
            return False


    def update_compatibility(self, level, subject=None):
        """
        Update the compatibility level for a subject.  Level must be one of:

        'NONE','FULL','FORWARD', or 'BACKWARD'
        """
        if level not in VALID_LEVELS:
            raise ClientError("Invalid level specified: %s" % (str(level)))

        url = '/'.join([self.url,'config'])
        if subject:
            url += '/' + subject

        body = { "compatibility" : level }
        result,meta,code = self._send_request(url, method='PUT', body=body)
        return result['compatibility']

    def get_compatibility(self, subject=None):
        """
        Get the current compatibility level for a subject.  Result will be one of:

        'NONE','FULL','FORWARD', or 'BACKWARD'
        """
        url = '/'.join([self.url,'config'])
        if subject:
            url += '/' + subject

        result,meta,code = self._send_request(url)
        compatibility = result.get('compatibility', None)
        if not compatibility:
            compatbility = result.get('compatibilityLevel')

        return compatbility
