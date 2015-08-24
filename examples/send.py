#!/usr/bin/env python
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
""" Minimal message send example code."""

import optparse
import logging
import sys
import uuid

from proton import Message
import pyngus
from utils import connect_socket
from utils import get_host_port
from utils import process_connection
from utils import SEND_STATUS

LOG = logging.getLogger()
LOG.addHandler(logging.StreamHandler())


class ConnectionEventHandler(pyngus.ConnectionEventHandler):
    def connection_failed(self, connection, error):
        """Connection's transport has failed in some way."""
        LOG.warn("Connection failed: %s", error)
        connection.close()

    def connection_remote_closed(self, connection, pn_condition):
        """Peer has closed its end of the connection."""
        LOG.debug("connection_remote_closed condition=%s", pn_condition)
        connection.close()


class SenderEventHandler(pyngus.SenderEventHandler):
    def sender_remote_closed(self, sender_link, pn_condition):
        LOG.debug("Sender peer_closed condition=%s", pn_condition)
        sender_link.close()

    def sender_failed(self, sender_link, error):
        """Protocol error occurred."""
        LOG.debug("Sender failed error=%s", error)
        sender_link.close()


def main(argv=None):

    _usage = """Usage: %prog [options] [message content string]"""
    parser = optparse.OptionParser(usage=_usage)
    parser.add_option("-a", dest="server", type="string",
                      default="amqp://0.0.0.0:5672",
                      help="The address of the server [amqp://0.0.0.0:5672]")
    parser.add_option("--idle", dest="idle_timeout", type="int",
                      default=0,
                      help="Idle timeout for connection (seconds).")
    parser.add_option("--debug", dest="debug", action="store_true",
                      help="enable debug logging")
    parser.add_option("--source", dest="source_addr", type="string",
                      help="Address for link source.")
    parser.add_option("--target", dest="target_addr", type="string",
                      help="Address for link target.")
    parser.add_option("--trace", dest="trace", action="store_true",
                      help="enable protocol tracing")
    parser.add_option("--ca",
                      help="Certificate Authority PEM file")
    parser.add_option("--username", type="string",
                      help="User Id for authentication")
    parser.add_option("--password", type="string",
                      help="User password for authentication")
    parser.add_option("--sasl-mechs", type="string",
                      help="The list of acceptable SASL mechs")

    opts, payload = parser.parse_args(args=argv)
    if not payload:
        payload = "Hi There!"
    if opts.debug:
        LOG.setLevel(logging.DEBUG)

    host, port = get_host_port(opts.server)
    my_socket = connect_socket(host, port)

    # create AMQP Container, Connection, and SenderLink
    #
    container = pyngus.Container(uuid.uuid4().hex)
    conn_properties = {'hostname': host,
                       'x-server': False}
    if opts.trace:
        conn_properties["x-trace-protocol"] = True
    if opts.ca:
        conn_properties["x-ssl-ca-file"] = opts.ca
    if opts.idle_timeout:
        conn_properties["idle-time-out"] = opts.idle_timeout
    if opts.username:
        conn_properties['x-username'] = opts.username
    if opts.password:
        conn_properties['x-password'] = opts.password
    if opts.sasl_mechs:
        conn_properties['x-sasl-mechs'] = opts.sasl_mechs

    c_handler = ConnectionEventHandler()
    connection = container.create_connection("sender",
                                             c_handler,
                                             conn_properties)
    connection.open()

    source_address = opts.source_addr or uuid.uuid4().hex
    s_handler = SenderEventHandler()
    sender = connection.create_sender(source_address,
                                      opts.target_addr,
                                      s_handler)
    sender.open()

    # Send a single message:
    msg = Message()
    msg.body = str(payload)

    class SendCallback(object):
        def __init__(self):
            self.done = False
            self.status = None

        def __call__(self, link, handle, status, error):
            self.done = True
            self.status = status

    cb = SendCallback()
    sender.send(msg, cb)

    # Poll connection until SendCallback is invoked:
    while not cb.done and not connection.closed:
        process_connection(connection, my_socket)

    if cb.done:
        print("Send done, status=%s" % SEND_STATUS.get(cb.status,
                                                       "???"))
    else:
        print("Send failed due to connection failure!")

    sender.close()
    connection.close()

    # Poll connection until close completes:
    while not connection.closed:
        process_connection(connection, my_socket)

    sender.destroy()
    connection.destroy()
    container.destroy()
    my_socket.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
