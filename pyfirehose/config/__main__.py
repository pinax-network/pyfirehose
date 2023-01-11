"""
SPDX-License-Identifier: MIT
"""

import logging
import os.path
from types import MethodType
from typing import Optional

import grpc
import hjson
from grpc_reflection.v1alpha.proto_reflection_descriptor_database import ProtoReflectionDescriptorDatabase
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.descriptor_pool import DescriptorPool
from npyscreen import ActionFormV2, FormWithMenus, NPSAppManaged
from npyscreen import MiniButtonPress, MultiLine, Pager, SelectOne, Slider, Textfield, TitleFilenameCombo, TitlePager, TitleSelectOne
from npyscreen import OptionBoolean, OptionFreeText, OptionList, OptionListDisplay, OptionSingleChoice, OptionMultiFreeList
from npyscreen import notify_confirm, notify_yes_no
from npyscreen.wgwidget import NotEnoughSpaceForWidget

# Prevent circular import with 'check_period'
from pyfirehose.args import check_period
from pyfirehose.config.utils import Config, StubConfig
from pyfirehose.config.utils import load_config, load_stub_config
from pyfirehose.utils import get_auth_token

import curses
from pygments import highlight
from pygments.lexers.data import JsonLexer
from pygments.formatters import TerminalFormatter

class EndpointsSelectOne(SelectOne):
    def display_value(self, vl: dict):
        try:
            return f'{vl["chain"]} ({vl["url"]})'
        except KeyError:
            return str(vl)

class EndpointsTitleSelectOne(TitleSelectOne):
    _entry_type = EndpointsSelectOne

class InputsListDisplay(OptionListDisplay):
    def __init__(self, *args, **kwargs):
        super(InputsListDisplay, self).__init__(*args, **kwargs)
        self._contained_widgets.ANNOTATE_WIDTH = 50

class InputBoolean(OptionBoolean):
    def when_set(self):
        if not isinstance(self.value, bool):
            self.value = str(self.value).lower() == 'true'

class InputFloat(OptionFreeText):
    def set(self, value):
        if value:
            try:
                float(value)
            except ValueError:
                logging.error('[%s] Trying to set a value that is not a FLOAT : %s', self.name, value)
                notify_confirm('Value entered is not a valid FLOAT', title=f'{self.name} validation error')

                return True

        self.value = value
        self.when_set()

        return False

    def set_from_widget_value(self, vl):
        """
        Method override allowing to quit or continue the option editing depending on the return value.

        See `on_ok_input_validation_hook`.
        """
        return self.set(vl)

    def _set_up_widget_values(self, option_form, main_option_widget):
        """
        Method override to replace the option form `on_ok` event handler with our own hook.

        See `on_ok_input_validation_hook`.
        """
        main_option_widget.value = self.value
        option_form.on_ok = MethodType(on_ok_input_validation_hook, option_form)

class InputInteger(OptionFreeText):
    def set(self, value):
        if value:
            try:
                int(value)
            except ValueError:
                logging.error('[%s] Trying to set a value that is not an INTEGER : %s', self.name, value)
                notify_confirm('Value entered is not a valid INTEGER', title=f'{self.name} validation error')

                # Returning `True` here allows to keep the editing form alive and prevent invalid input to be accepted
                return True

        self.value = value
        self.when_set()

        return False

    def set_from_widget_value(self, vl):
        """
        Method override allowing to quit or continue the option editing depending on the return value.

        See `on_ok_input_validation_hook`.
        """
        return self.set(vl)

    def _set_up_widget_values(self, option_form, main_option_widget):
        """
        Method override to replace the option form `on_ok` event handler with our own hook.

        See `on_ok_input_validation_hook`.
        """
        main_option_widget.value = self.value
        option_form.on_ok = MethodType(on_ok_input_validation_hook, option_form)

def on_ok_input_validation_hook(self,):
    """
    Hook to replace the `on_ok` event handler for validating an option input.

    It returns the value of the `Option.set` function to continue or stop the editing.
    Used to prevent entering invalid input for options.
    """
    return self.OPTION_TO_CHANGE.set_from_widget_value(self.OPTION_WIDGET.value)

def mkcolor(default_color, offset=49):
    """
    Adapted from Cansi library (https://github.com/tslight/cansi)
    """
    color = {}

    curses.use_default_colors()  # https://stackoverflow.com/a/44015131
    for i in range(1, 8):
        curses.init_pair(i + offset, i, -1)  # color fg on black bg
        curses.init_pair(i + offset + 7, curses.COLOR_WHITE, i)  # white fg on color bg
        curses.init_pair(i + offset + 14, curses.COLOR_BLACK, i)  # black fg on color bg
        color[str(i + 30)] = curses.color_pair(i + offset)
        color[str(i + 40)] = curses.color_pair(i + offset + 7)
        color["0;" + str(i + 30)] = curses.color_pair(i + offset)
        color["0;" + str(i + 40)] = curses.color_pair(i + offset + 7)
        color[str(i + 30) + ";0"] = curses.color_pair(i + offset)
        color[str(i + 40) + ";0"] = curses.color_pair(i + offset + 7)
        color[str(i + 90)] = curses.color_pair(i + offset) | curses.A_BOLD
        color["1;" + str(i + 30)] = curses.color_pair(i + offset) | curses.A_BOLD
        color["1;" + str(i + 40)] = curses.color_pair(i + offset + 7) | curses.A_BOLD
        color[str(i + 30) + ";1"] = curses.color_pair(i + offset) | curses.A_BOLD
        color[str(i + 40) + ";1"] = curses.color_pair(i + offset + 7) | curses.A_BOLD

        color["39;49;00"] = default_color

    return color


def colorize(default_color, string):
    """
    Adapted from Cansi library (https://github.com/tslight/cansi)
    """
    ansi_split = string.split("\x1b[")
    color_pair = curses.color_pair(0)

    color = mkcolor(default_color)
    attr = {
        "1": curses.A_BOLD,
        "4": curses.A_UNDERLINE,
        "5": curses.A_BLINK,
        "7": curses.A_REVERSE,
    }
    colors = []

    for substring in ansi_split[1:]:
        if substring.startswith("0K"):
            return  # 0K = clrtoeol so we are done with this line

        ansi_code = substring.split("m")[0]
        substring = substring[len(ansi_code) + 1 :]
        if ansi_code in ["1", "4", "5", "7", "8"]:
            color_pair = color_pair | attr[ansi_code]
        elif ansi_code not in ["0", "0;"]:
            color_pair = color[ansi_code]

        if substring:
            colors.append((color_pair, len(substring)))

    return colors

class CodeHighlightedTextfield(Textfield):
    def __init__(self, *args, **kwargs):
        super(CodeHighlightedTextfield, self).__init__(*args, **kwargs)
        self.syntax_highlighting = True

    def update_highlighting(self, start, end):
        substr = self._get_string_to_print()
        if not substr in self.parent.stored_highlights:
            return

        self._highlightingdata = self.parent.stored_highlights[substr]

class CodeHighlightedPager(Pager):
    _contained_widgets = CodeHighlightedTextfield

class CodeHighlightedTitlePager(TitlePager):
    _entry_type = CodeHighlightedPager

class MainForm(FormWithMenus):
    OK_BUTTON_TEXT = 'Quit'

    def afterEditing(self):
        self.parentApp.setNextForm(self.next_form)

    def beforeEditing(self):
        self.next_form = None

    def create(self):
        self.main_menu = self.new_menu(name='Main menu')
        self.main_menu.addItem(
            text='Edit main config',
            onSelect=self.switch_form,
            arguments=[self.parentApp.MAIN_CONFIG_EDIT_FORM]
        )
        self.main_menu.addItem(
            text='Edit stub config',
            onSelect=self.switch_form,
            arguments=[self.parentApp.STUB_CONFIG_ENPOINTS_FORM]
        )


        main_config_text = hjson.dumpsJSON(self.parentApp.main_config, indent=4)
        main_config_text_split = main_config_text.split('\n')
        main_config_highlighted_text_split = highlight(main_config_text, JsonLexer(), TerminalFormatter()).split('\n')

        self.stored_highlights = {}
        for i in range(len(main_config_highlighted_text_split) - 1):
            self.stored_highlights[main_config_text_split[i]] = [
                c for (color, length) in colorize(
                    self.theme_manager.findPair(self, 'DEFAULT'),
                    main_config_highlighted_text_split[i]
                ) for c in [color] * length
            ]

        self.add(
            CodeHighlightedTitlePager,
            name='Main config (view only)',
            values=main_config_text_split,
        )

    def switch_form(self, form: str) -> None:
        self.next_form = form
        self.parentApp.switchForm(form)

class StubConfigEndpointsForm(ActionFormV2):
    def create(self):	
        self.ml_endpoints = self.add(
            EndpointsTitleSelectOne,
            name='Select an endpoint',
            values=self.parentApp.main_config['grpc'],
            value=[0],
            scroll_exit=True
        )

    def on_ok(self):
        self.parentApp.selected_endpoint = self.ml_endpoints.values[self.ml_endpoints.value.pop()]
        logging.info('[%s] Selected endpoint : %s', self.name, self.parentApp.selected_endpoint)

        self.parentApp.addForm(
            self.parentApp.STUB_CONFIG_SAVE_FILE_FORM,
            StubConfigSaveFileForm, name='Stub config editing - Save file'
        )
        self.parentApp.setNextForm(self.parentApp.STUB_CONFIG_SAVE_FILE_FORM)

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

class StubConfigSaveFileForm(ActionFormV2):
    def create(self):
        try:
            endpoint_id = self.parentApp.selected_endpoint['id']
        except AttributeError:
            logging.error('[%s] No endpoint selected', self.name)
        except KeyError:
            logging.error('[%s] Could not get id from endpoint : %s', self.name, self.parentApp.selected_endpoint)

        self.stub_loaded = load_config(self.parentApp.main_config_file, endpoint_id)
        try:
            saved_stub = next((e['stub'] for e in self.parentApp.main_config['grpc'] if e['id'] == endpoint_id), None)
        except KeyError:
            saved_stub = None

        self.tfc_stub_save_file = self.add(TitleFilenameCombo, name='Save to file', value=saved_stub)

    def on_ok(self):
        self.parentApp.stub_save_file = self.tfc_stub_save_file.value
        logging.info('[%s] Stub save file : %s', self.name, self.parentApp.stub_save_file)

        try:
            load_stub_config(self.parentApp.stub_save_file)
        except FileNotFoundError:
            if self.stub_loaded:
                # If user wants to edit new config than the one loaded, reset it
                # TODO : Ask user if wants to keep previous loaded values for new file (?)
                StubConfig.REQUEST_PARAMETERS = {}

        self.parentApp.stub_config = {}
        if os.path.isfile(self.parentApp.stub_save_file):
            with open(self.parentApp.stub_save_file, 'r', encoding='utf8') as config_file:
                try:
                    self.parentApp.stub_config = hjson.load(config_file)
                except hjson.HjsonDecodeError as error:
                    logging.exception('Error decoding stub config file (%s): %s', self.parentApp.stub_save_file, error)
                    raise

        self.parentApp.addForm(
            self.parentApp.STUB_CONFIG_SERVICES_FORM,
            StubConfigServicesForm, name='Stub config editing - Services'
        )
        self.parentApp.setNextForm(self.parentApp.STUB_CONFIG_SERVICES_FORM)

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

class StubConfigServicesForm(ActionFormV2):
    def create(self):
        jwt = get_auth_token()
        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.access_token_call_credentials(jwt)
        )

        channel = grpc.secure_channel(Config.GRPC_ENDPOINT, creds)
        self.parentApp.reflection_db = ProtoReflectionDescriptorDatabase(channel)

        services = self.parentApp.reflection_db.get_services()
        self.ml_services = self.add(
            TitleSelectOne,
            name='Select a service',
            values=services,
            value=[0],
            scroll_exit=True
        )

    def on_ok(self):
        self.parentApp.selected_service = self.ml_services.values[self.ml_services.value.pop()]
        logging.info('[%s] Selected service : %s', self.name, self.parentApp.selected_service)

        self.parentApp.addForm(
            self.parentApp.STUB_CONFIG_METHODS_FORM,
            StubConfigMethodsForm, name='Stub config editing - Methods'
        )
        self.parentApp.setNextForm(self.parentApp.STUB_CONFIG_METHODS_FORM)

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

class StubConfigMethodsForm(ActionFormV2):
    def create(self):
        desc_pool = DescriptorPool(self.parentApp.reflection_db)
        self.methods = desc_pool.FindServiceByName(self.parentApp.selected_service).methods

        self.ml_services = self.add(
            TitleSelectOne,
            name='Select a method',
            values=[m.name for m in self.methods],
            value=[0],
            scroll_exit=True
        )

    def on_ok(self):
        self.parentApp.selected_method = next(
            (m for m in self.methods if m.name == self.ml_services.values[self.ml_services.value.pop()]),
            None
        )
        logging.info('[%s] Selected method : %s', self.name, self.parentApp.selected_method)

        self.parentApp.addForm(
            self.parentApp.STUB_CONFIG_INPUTS_FORM,
            StubConfigInputsForm, name='Stub config editing - Inputs'
        )
        self.parentApp.setNextForm(self.parentApp.STUB_CONFIG_INPUTS_FORM)

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

class StubConfigInputsForm(ActionFormV2):
    def create(self):
        options = OptionList().options

        for input_parameter in [
            f for f in self.parentApp.selected_method.input_type.fields if not f.name in ('start_block_num', 'stop_block_num')
        ]:
            try:
                stub_config_value = self.parentApp.stub_config['parameters'][input_parameter.name]
            except KeyError:
                stub_config_value = None

            logging.info('[%s] %s:%s / value = %s:%s',
                self.name,
                type_to_string(input_parameter.type),
                input_parameter.name,
                type(stub_config_value),
                stub_config_value
            )

            try:
                if input_parameter.type == FieldDescriptor.TYPE_BOOL:
                    options.append(
                        InputBoolean(
                            name=input_parameter.name,
                            value=stub_config_value,
                            documentation=[
                                f'A parameter of type BOOL is expected for "{input_parameter.name}."',
                                'Press [X] or [SPACE] to toggle between checked/unchecked.'
                            ]
                        )
                    )
                elif input_parameter.type in [
                    FieldDescriptor.TYPE_DOUBLE,
                    FieldDescriptor.TYPE_FLOAT,
                ]:
                    options.append(
                        InputFloat(
                            name=input_parameter.name,
                            value=stub_config_value,
                            documentation=[
                                f'A parameter of type FLOAT is expected for "{input_parameter.name}."',
                            ]
                        )
                    )
                elif input_parameter.type in [
                    FieldDescriptor.TYPE_INT32,
                    FieldDescriptor.TYPE_INT64,
                    FieldDescriptor.TYPE_FIXED32,
                    FieldDescriptor.TYPE_FIXED64,
                    FieldDescriptor.TYPE_SFIXED32,
                    FieldDescriptor.TYPE_SFIXED64,
                    FieldDescriptor.TYPE_SINT32,
                    FieldDescriptor.TYPE_SINT64,
                    FieldDescriptor.TYPE_UINT32,
                    FieldDescriptor.TYPE_UINT64,
                ]:
                    options.append(
                        InputInteger(
                            name=input_parameter.name,
                            value=stub_config_value,
                            documentation=[
                                f'A parameter of type INTEGER is expected for "{input_parameter.name}."',
                            ]
                        )
                    )
                elif input_parameter.type == FieldDescriptor.TYPE_ENUM:
                    enum_choices = [e.name for e in input_parameter.enum_type.values]
                    options.append(
                        OptionSingleChoice(
                            name=input_parameter.name,
                            value=stub_config_value,
                            documentation=[
                                f'A parameter of type ENUM is expected for "{input_parameter.name}."',
                                f'Valid values are {enum_choices}.'
                            ],
                            choices=enum_choices
                        )
                    )
                else:
                    # Remaining types : TYPE_BYTES, TYPE_GROUP, TYPE_MESSAGE, TYPE_STRING
                    options.append(
                        OptionFreeText(
                            name=input_parameter.name,
                            value=stub_config_value,
                            documentation=[
                                f'A parameter of type STRING is expected for "{input_parameter.name}."',
                            ]
                        )
                    )

                if input_parameter.label == FieldDescriptor.LABEL_REPEATED:
                    input_option = options.pop()
                    options.append(
                        OptionMultiFreeList( # TODO: Make custom option for validating repeated fields (depends on input type)
                            name=input_option.name,
                            value=input_option.value,
                            documentation=input_option.documentation
                        )
                    )

            except NotEnoughSpaceForWidget as error:
                logging.error('[%s] Could not allocate space for %s : %s', self.name, input_parameter.name, error)

        self.w_inputs = self.add(InputsListDisplay, name='Edit method inputs', values=options, scroll_exit=True)

    def on_ok(self):
        if not self.parentApp.stub_config:
            self.parentApp.stub_config['parameters'] = {}
            self.parentApp.stub_config['python_import_dir'], self.parentApp.stub_config['name'] = self.parentApp.selected_service.rsplit('.', 1)
            self.parentApp.stub_config['request'] = self.parentApp.selected_method.input_type.name

        for input_option in self.w_inputs.values:
            if input_option.value:
                self.parentApp.stub_config['parameters'][input_option.name] = input_option.value

        logging.info('[%s] Stub config : %s', self.name, self.parentApp.stub_config)

        self.parentApp.addForm(
            self.parentApp.STUB_CONFIG_CONFIRM_EDIT_FORM,
            StubConfigConfirmEditForm, name='Stub config editing - Confirm'
        )
        self.parentApp.setNextForm(self.parentApp.STUB_CONFIG_CONFIRM_EDIT_FORM)

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

# TODO : Add output config screen

class ActionFormDiscard(ActionFormV2):
    class Discard_Button(MiniButtonPress):
        def whenPressed(self):
            self.parent._on_discard()

    DISCARDBUTTON_TYPE = Discard_Button
    DISCARD_BUTTON_BR_OFFSET = (
        ActionFormV2.CANCEL_BUTTON_BR_OFFSET[0],
        ActionFormV2.OK_BUTTON_BR_OFFSET[1]
        + len(ActionFormV2.OK_BUTTON_TEXT)
        + ActionFormV2.CANCEL_BUTTON_BR_OFFSET[1]
        + len(ActionFormV2.CANCEL_BUTTON_TEXT)
    )
    DISCARD_BUTTON_TEXT = 'Discard'

    def _on_discard(self):
        self.editing = self.on_discard()

    def create_control_buttons(self):
        self._add_button('ok_button',
            self.__class__.OKBUTTON_TYPE,
            self.__class__.OK_BUTTON_TEXT,
            0 - self.__class__.OK_BUTTON_BR_OFFSET[0],
            0 - self.__class__.OK_BUTTON_BR_OFFSET[1] - len(self.__class__.OK_BUTTON_TEXT),
            None
        )

        self._add_button('cancel_button',
            self.__class__.CANCELBUTTON_TYPE,
            self.__class__.CANCEL_BUTTON_TEXT,
            0 - self.__class__.CANCEL_BUTTON_BR_OFFSET[0],
            0 - self.__class__.CANCEL_BUTTON_BR_OFFSET[1] - len(self.__class__.CANCEL_BUTTON_TEXT),
            None
        )

        self._add_button('discard_button',
            self.__class__.DISCARDBUTTON_TYPE,
            self.__class__.DISCARD_BUTTON_TEXT,
            0 - self.__class__.DISCARD_BUTTON_BR_OFFSET[0],
            0 - self.__class__.DISCARD_BUTTON_BR_OFFSET[1] - len(self.__class__.DISCARD_BUTTON_TEXT),
            None
        )

    def on_discard(self):
        return False

class StubConfigConfirmEditForm(ActionFormDiscard):
    def create(self):
        stub_config_text = hjson.dumpsJSON(self.parentApp.stub_config, indent=4)
        stub_config_text_split = stub_config_text.split('\n')
        stub_config_highlighted_text_split = highlight(stub_config_text, JsonLexer(), TerminalFormatter()).split('\n')

        self.stored_highlights = {}
        for i in range(len(stub_config_highlighted_text_split) - 1):
            self.stored_highlights[stub_config_text_split[i]] = [
                c for (color, length) in colorize(
                    self.theme_manager.findPair(self, 'DEFAULT'),
                    stub_config_highlighted_text_split[i]
                ) for c in [color] * length
            ]

        self.add(
            CodeHighlightedTitlePager,
            name='Stub config recap (view only)',
            values=stub_config_text_split,
        )

    def on_ok(self):
        with open(self.parentApp.stub_save_file, 'w+', encoding='utf8') as config_file:
            hjson.dumpJSON(self.parentApp.stub_config, config_file, indent=4)

        self.parentApp.setNextForm('MAIN')

    def on_cancel(self):
        self.parentApp.setNextFormPrevious()

    def on_discard(self):
        discard_confirm = notify_yes_no(
            'Do you really want to discard this stub ? (No changes will be saved)',
            title=f'Discard "{self.parentApp.stub_save_file}" ?'
        )
        if discard_confirm:
            self.parentApp.switchForm('MAIN')
        else:
            pass

class ConfigApp(NPSAppManaged):
    STUB_CONFIG_CONFIRM_EDIT_FORM = 'STUB_CONFIG_CONFIRM_EDIT_FORM'
    STUB_CONFIG_ENPOINTS_FORM = 'STUB_CONFIG_ENDPOINTS_FORM'
    STUB_CONFIG_INPUTS_FORM = 'STUB_CONFIG_INPUTS_FORM'
    STUB_CONFIG_METHODS_FORM = 'STUB_CONFIG_METHODS_FORM'
    STUB_CONFIG_SAVE_FILE_FORM = 'STUB_CONFIG_SAVE_FILE_FORM'
    STUB_CONFIG_SERVICES_FORM = 'STUB_CONFIG_SERVICES_FORM'
    MAIN_CONFIG_EDIT_FORM = 'MAIN_CONFIG_EDIT_FORM'

    def __init__(self):
        super().__init__()

        self.main_config_file = 'pyfirehose/config.hjson'
        with open(self.main_config_file, 'r', encoding='utf8') as config_file:
            try:
                self.main_config = hjson.load(config_file)
            except hjson.HjsonDecodeError as error:
                logging.exception('Error decoding main config file (%s): %s', self.main_config_file, error)
                raise

    def onStart(self):
        self.addForm('MAIN', MainForm, name='PyFirehose config')
        self.addForm(self.STUB_CONFIG_ENPOINTS_FORM, StubConfigEndpointsForm, name='Stub config editing - Endpoints')
        # self.addForm(self.MAIN_CONFIG_EDIT_FORM, mainEditForm, name='PyFirehose config')

def type_to_string(type_: int) -> str:
    return dict( #pylint: disable=consider-using-dict-comprehension
        [(value, key.split('_')[1]) for key, value in vars(FieldDescriptor).items() if 'TYPE_' in key and not 'CPP' in key]
    )[type_]

if __name__ == '__main__':
    logging.basicConfig(
        handlers=[logging.FileHandler('logs/config.log', mode='w')],
        level=logging.INFO,
        format='%(asctime)s:T+%(relativeCreated)d %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True
    )

    logging.addLevelName(logging.DEBUG, '[DEBUG]')
    logging.addLevelName(logging.INFO, '[*]')
    logging.addLevelName(logging.WARNING, '[!]')
    logging.addLevelName(logging.ERROR, '[ERROR]')
    logging.addLevelName(logging.CRITICAL, '[CRITICAL]')

    APP = ConfigApp().run()
