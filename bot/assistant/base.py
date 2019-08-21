# Copyright (C) 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Sample that implements a text client for the Google Assistant Service."""

import os
import logging
import json

import click
import google.auth.transport.grpc
import google.auth.transport.requests
import google.oauth2.credentials

from google.assistant.embedded.v1alpha2 import (
	embedded_assistant_pb2,
	embedded_assistant_pb2_grpc
)

from bot.assistant import assistant_helpers
from bot.assistant import browser_helpers


PLAYING = embedded_assistant_pb2.ScreenOutConfig.PLAYING


class AlphaAssistant(object):
	"""Sample Assistant that supports text based conversations.
	Args:
	  language_code: language for the conversation.
	  device_model_id: identifier of the device model.
	  device_id: identifier of the registered device instance.
	  display: enable visual display of assistant response.
	  channel: authorized gRPC channel for connection to the
		Google Assistant API.
	  deadline_sec: gRPC deadline in seconds for Google Assistant API call.
	"""

	def __init__(self, language_code, device_model_id, device_id,
				 display, channel, deadline_sec):
		self.language_code = language_code
		self.device_model_id = device_model_id
		self.device_id = device_id
		self.conversation_state = None
		# Force reset of first conversation.
		self.is_new_conversation = True
		self.display = display
		self.assistant = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(
			channel
		)
		self.deadline = deadline_sec

	def __enter__(self):
		return self

	def __exit__(self, etype, e, traceback):
		if e:
			return False

	def assist(self, text_query):
		"""Send a text request to the Assistant and playback the response.
		"""
		def iter_assist_requests():
			config = embedded_assistant_pb2.AssistConfig(
				audio_out_config=embedded_assistant_pb2.AudioOutConfig(
					encoding='LINEAR16',
					sample_rate_hertz=16000,
					volume_percentage=0,
				),
				dialog_state_in=embedded_assistant_pb2.DialogStateIn(
					language_code=self.language_code,
					conversation_state=self.conversation_state,
					is_new_conversation=self.is_new_conversation,
				),
				device_config=embedded_assistant_pb2.DeviceConfig(
					device_id=self.device_id,
					device_model_id=self.device_model_id,
				),
				text_query=text_query,
			)
			# Continue current conversation with later requests.
			self.is_new_conversation = False
			if self.display:
				config.screen_out_config.screen_mode = PLAYING
			req = embedded_assistant_pb2.AssistRequest(config=config)
			assistant_helpers.log_assist_request_without_audio(req)
			yield req

		text_response = None
		html_response = None
		for resp in self.assistant.Assist(iter_assist_requests(),
										  self.deadline):
			assistant_helpers.log_assist_response_without_audio(resp)
			if resp.screen_out.data:
				html_response = resp.screen_out.data
			if resp.dialog_state_out.conversation_state:
				conversation_state = resp.dialog_state_out.conversation_state
				self.conversation_state = conversation_state
			if resp.dialog_state_out.supplemental_display_text:
				text_response = resp.dialog_state_out.supplemental_display_text
		return text_response, html_response
