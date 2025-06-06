#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from time import sleep

from lmdeploy import GenerationConfig, TurbomindEngineConfig, pipeline

from dubbo import Dubbo
from dubbo.configs import RegistryConfig, ServiceConfig
from dubbo.proxy.handlers import RpcMethodHandler, RpcServiceHandler
import chat_pb2

# the path of a model. It could be one of the following options:
# 1. A local directory path of a turbomind model
# 2. The model_id of a lmdeploy-quantized model
# 3. The model_id of a model hosted inside a model repository
model = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"

backend_config = TurbomindEngineConfig(cache_max_entry_count=0.2, max_context_token_num=20544, session_len=20544)

gen_config = GenerationConfig(
    top_p=0.95,
    temperature=0.6,
    max_new_tokens=8192,
    stop_token_ids=[151329, 151336, 151338],
    do_sample=True,  # enable sampling
)


class DeepSeekAiServicer:
    def __init__(self, model: str, backend_config: TurbomindEngineConfig, gen_config: GenerationConfig):
        self.llm = pipeline(model, backend_config=backend_config)
        self.gen_config = gen_config

    def chat(self, stream):
        # read request from stream
        request = stream.read()
        print(f"Received request: {request}")
        # prepare prompts
        prompts = [{"role": request.role, "content": request.content + "<think>\n"}]

        is_think = False

        # perform streaming inference
        for item in self.llm.stream_infer(prompts, gen_config=gen_config):
            # update think status
            if item.text == "<think>":
                is_think = True
                continue
            elif item.text == "</think>":
                is_think = False
                continue
            # According to the state of thought, decide the content of the reply.
            if is_think:
                # send thought
                stream.write(chat_pb2.ChatReply(think=item.text, answer=""))
            else:
                # send answer
                stream.write(chat_pb2.ChatReply(think="", answer=item.text))

        stream.done_writing()


def build_server_handler():
    # build a method handler
    deepseek_ai_servicer = DeepSeekAiServicer(model, backend_config, gen_config)
    method_handler = RpcMethodHandler.server_stream(
        deepseek_ai_servicer.chat,
        method_name="chat",
        request_deserializer=chat_pb2.ChatRequest.FromString,
        response_serializer=chat_pb2.ChatReply.SerializeToString,
    )
    # build a service handler
    service_handler = RpcServiceHandler(
        service_name="org.apache.dubbo.samples.llm.api.DeepSeekAiService",
        method_handlers=[method_handler],
    )
    return service_handler


if __name__ == "__main__":
    # build a service handler
    service_handler = build_server_handler()
    service_config = ServiceConfig(service_handler=service_handler)

    # Configure the Zookeeper registry
    registry_config = RegistryConfig.from_url("zookeeper://zookeeper:2181")
    bootstrap = Dubbo(registry_config=registry_config)

    # Create and start the server
    bootstrap.create_server(service_config).start()

    # 30days
    sleep(30 * 24 * 60 * 60)
