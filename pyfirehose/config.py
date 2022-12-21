"""
SPDX-License-Identifier: MIT

This module parses the main config and stub config files for use by the application.
Refer to the README.md and comments within the config files for more details about each parameters.
"""

import logging
from argparse import ArgumentTypeError
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

# https://hjson.github.io/hjson-py/ -- allow comments in JSON files for configuration purposes
import hjson
from grpc import Compression

# Prevent circular import between utils and config modules
import pyfirehose.utils as utils #pylint: disable=consider-using-from-import

@dataclass
class StubConfig:
    """
    Holds the stub config.
    """
    REQUEST_OBJECT: ClassVar[Any]
    REQUEST_PARAMETERS: ClassVar[dict]
    STUB_OBJECT: ClassVar[Any]

@dataclass
class Config:
    """
    Holds the main config.
    """
    API_KEY: ClassVar[str]
    AUTH_ENDPOINT: ClassVar[str]
    CHAIN: ClassVar[str]
    COMPRESSION: ClassVar[Compression]
    GRAPHQL_ENDPOINT: ClassVar[str]
    GRAPHQL_CACHE: ClassVar[int]
    GRPC_ENDPOINT: ClassVar[str]
    MAX_BLOCK_SIZE: ClassVar[int]
    MAX_FAILED_BLOCK_RETRIES: ClassVar[int]

def load_config(file: str, grpc_entry_id: Optional[str] = None) -> bool:
    """
    Load the main config from the specified file. If a gRPC entry id is specified, it overwrites the default specified
    in the config.

    Args:
        file: Filepath to the main config file.
        grpc_entry_id: Id of a gRPC entry present in the "grpc" array of the main config file.

    Returns:
        A boolean indicating if the stub config file has also been loaded.

    Raises:
        ArgumentTypeError: If the specified compression argument for a gRPC endpoint is not one of "gzip" or "deflate".
        HjsonDecodeError: If the hjson module fails to parse the config file.
        ImportError: If the stub config files fails to import the specified modules.
        KeyError: If a required key is missing from the config file.
    """
    with open(file, 'r', encoding='utf8') as config_file:
        try:
            options = hjson.load(config_file)
        except hjson.HjsonDecodeError as error:
            logging.exception('Error decoding main config file (%s): %s', file, error)
            raise

    try:
        if grpc_entry_id:
            options['default'] = grpc_entry_id

        default_grpc_id = [i for i, entry in enumerate(options['grpc']) if entry['id'] == options['default']][0]
        default_grpc = options['grpc'][default_grpc_id]
        default_auth = options['auth'][default_grpc['auth']]
        default_stub = default_grpc['stub'] if 'stub' in default_grpc else ''

        Config.API_KEY 					= default_auth['api_key']
        Config.AUTH_ENDPOINT 			= default_auth['endpoint']
        Config.CHAIN 					= default_grpc['chain']
        Config.GRAPHQL_ENDPOINT 		= options['graphql_endpoint']
        Config.GRAPHQL_CACHE 			= options['graphql_cache']
        Config.GRPC_ENDPOINT 			= default_grpc['url']
        Config.MAX_BLOCK_SIZE 			= options['max_block_size']
        Config.MAX_FAILED_BLOCK_RETRIES = options['max_failed_block_retries']
    except KeyError as error:
        logging.exception('Error parsing main config file (%s): %s', file, error)
        raise

    try:
        compression_option = default_grpc['compression'].lower()
        if compression_option in ['gzip', 'deflate']:
            Config.COMPRESSION = Compression.Gzip if compression_option == 'gzip' else Compression.Deflate
        else:
            logging.exception('Unrecognized compression option: "%s" not one of "gzip" or "deflate"', compression_option)
            raise ArgumentTypeError
    except KeyError as error:
        Config.COMPRESSION = Compression.NoCompression

    logging.debug('Loaded main config: %s [SUCCESS]', vars(Config))

    if default_stub:
        load_stub_config(default_stub)
    else:
        return False

    return True

def load_stub_config(stub: str | dict) -> None:
    """
    Load the stub config from a file (str) or directly from a key-value dictionary.

    Args:
        stub: The stub to load either as a filepath or a dictionary.

    Raises:
        HjsonDecodeError: If the hjson module fails to parse the config file.
        ImportError: If the specified stub or request object cannot be imported.
        KeyError: If a required key is missing from the config file.
    """
    stub_config = stub
    # Load stub config from external file
    if isinstance(stub, str):
        with open(stub, 'r', encoding='utf8') as stub_config_file:
            try:
                stub_config = hjson.load(stub_config_file)
            except hjson.HjsonDecodeError as error:
                logging.exception('Error decoding stub config file (%s): %s', stub, error)
                raise

    try:
        import_dir_module = f'pyfirehose.proto.generated.{stub_config["python_import_dir"]}'
        imported = utils.import_all_from_module(import_dir_module)

        for module in imported:
            try:
                StubConfig.STUB_OBJECT = getattr(module, f'{stub_config["name"]}Stub')
            except AttributeError:
                pass

            try:
                StubConfig.REQUEST_OBJECT = getattr(module, stub_config['request'])
            except AttributeError:
                pass

        if not StubConfig.STUB_OBJECT:
            logging.critical('Could not load stub object from config: unable to locate "%sStub" in "%s" module',
                stub_config['name'],
                f'pyfirehose.proto.generated.{stub_config["python_import_dir"]}')
            raise ImportError

        if not StubConfig.REQUEST_OBJECT:
            logging.critical('Could not load request object from config: unable to locate "%s"', stub_config['request'])
            raise ImportError

        StubConfig.REQUEST_PARAMETERS = {}
        for key, value in stub_config['parameters'].items():
            StubConfig.REQUEST_PARAMETERS[key] = value
    except ImportError as error:
        logging.exception('Error importing modules from specified directory (%s): %s',
            stub_config['python_import_dir'],
            error
        )
        raise
    except KeyError as error:
        logging.exception('Error parsing stub config (%s): %s', stub_config, error)
        raise

    logging.debug('Loaded stub config: %s [SUCCESS]', vars(StubConfig))
