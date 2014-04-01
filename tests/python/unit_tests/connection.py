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
import common
# import logging
import os
import time

from proton import SSLUnavailable, Condition
import dingus


class APITest(common.Test):

    def setup(self):
        # logging.getLogger("dingus").setLevel(logging.DEBUG)
        self.container1 = dingus.Container("test-container-1")
        self.container2 = dingus.Container("test-container-2")

    def teardown(self):
        if self.container1:
            self.container1.destroy()
        if self.container2:
            self.container2.destroy()

    def test_create_destroy(self):
        """Verify Connection construction/destruction."""
        conn = self.container1.create_connection("conn1")
        assert conn
        assert conn.container == self.container1
        assert conn.name == "conn1"
        conn.destroy()

    def test_open_close_polled(self):
        """Verify Connection open/close sans callbacks."""
        c1 = self.container1.create_connection("c1")
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        assert c1.active and not c1.closed
        assert c2.active and not c2.closed
        assert c1.remote_container == "test-container-2"
        assert c2.remote_container == "test-container-1"
        c1.close()
        c2.close()
        common.process_connections(c1, c2)
        assert c1.closed and not c1.active
        assert c2.closed and not c2.active

    def test_user_context(self):
        c1 = self.container1.create_connection("c1")
        c1.user_context = "Hi There"
        del c1
        c1 = self.container1.get_connection("c1")
        assert c1.user_context == "Hi There"

    def test_active_callback(self):
        c1_events = common.ConnCallback()
        c2_events = common.ConnCallback()
        c1 = self.container1.create_connection("c1", c1_events)
        c2 = self.container2.create_connection("c2", c2_events)
        c1.open()
        common.process_connections(c1, c2)
        assert c1_events.active_ct == 0
        assert c2_events.active_ct == 0
        c2.open()
        common.process_connections(c1, c2)
        assert c1.active and c1_events.active_ct == 1
        assert c2.active and c2_events.active_ct == 1
        assert c1_events.remote_closed_ct == 0
        c2.close()
        common.process_connections(c1, c2)
        assert (c1_events.remote_closed_ct == 1
                and c2_events.remote_closed_ct == 0)
        assert c1_events.closed_ct == 0 and c2_events.closed_ct == 0
        assert not c1.active and not c2.active
        c1.close()
        common.process_connections(c1, c2)
        assert c2_events.remote_closed_ct == 0
        assert c2_events.closed_ct > 0
        assert c1_events.closed_ct > 0
        # ensure that closing the input & output does not trigger an error at
        # this point:
        c1.close_input()
        c1.close_output()
        c1.process(time.time())
        assert c1_events.failed_ct == 0

    def test_sasl_callbacks(self):
        """Verify access to the connection's SASL state."""
        class SaslCallbackServer(common.ConnCallback):
            def sasl_step(self, connection, pn_sasl):
                creds = pn_sasl.recv()
                if creds == "\x00user-foo\x00pass-word":
                    pn_sasl.done(pn_sasl.OK)

        c1_events = SaslCallbackServer()
        c1 = self.container1.create_connection("c1", c1_events)
        pn_sasl = c1.pn_sasl
        assert pn_sasl
        pn_sasl.mechanisms("PLAIN")
        pn_sasl.server()

        class SaslCallbackClient(common.ConnCallback):
            def sasl_done(self, connection, pn_sasl, result):
                assert result == pn_sasl.OK

        c2_events = SaslCallbackClient()
        c2 = self.container2.create_connection("c2", c2_events)
        pn_sasl = c2.pn_sasl
        assert pn_sasl
        pn_sasl.plain("user-foo", "pass-word")

        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        assert c1.active and c2.active

    def test_properties_idle_timeout(self):
        props = {"idle-time-out": 3}
        c1 = self.container1.create_connection("c1", properties=props)
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        assert c1.active and c2.active
        assert c1.next_tick != 0 and c2.next_tick != 0

    def test_properties_hostname(self):
        props = {"hostname": "TomServo"}
        c1 = self.container1.create_connection("c1", properties=props)
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        assert c1.active and c2.active
        assert not c1.remote_hostname
        assert c2.remote_hostname == "TomServo"

    def test_create_sender(self):
        s_conn = self.container1.create_connection("s")
        r_events = common.ConnCallback()
        r_conn = self.container2.create_connection("r", r_events)

        s_conn.open()
        r_conn.open()
        props = {"distribution-mode": "copy"}
        sender = s_conn.create_sender("my-source",
                                      "req-target",
                                      name="amigo",
                                      properties=props)
        sender.open()
        common.process_connections(s_conn, r_conn)
        assert r_events.receiver_requested_ct == 1
        args = r_events.receiver_requested_args[0]
        assert args.name == "amigo"
        assert args.requested_target == "req-target"
        assert args.properties.get("distribution-mode") == "copy"
        assert args.properties.get("source-address") == "my-source"

    def test_create_receiver(self):
        s_events = common.ConnCallback()
        s_conn = self.container1.create_connection("s", s_events)
        r_conn = self.container2.create_connection("r")

        s_conn.open()
        r_conn.open()
        props = {"distribution-mode": "move"}
        receiver = r_conn.create_receiver("my-target",
                                          "req-source",
                                          name="amiga",
                                          properties=props)
        receiver.open()
        common.process_connections(s_conn, r_conn)
        assert s_events.sender_requested_ct == 1
        args = s_events.sender_requested_args[0]
        assert args.name == "amiga"
        assert args.requested_source == "req-source"
        assert args.properties.get("distribution-mode") == "move"
        assert args.properties.get("target-address") == "my-target"

    def _test_ssl(self,
                  server_password="server-password",
                  server_dns="some.server.com"):

        def _testpath(file):
            """ Set the full path to the PEM files."""
            return os.path.join(os.path.dirname(__file__),
                                "ssl_db/%s" % file)

        s_props = {"x-ssl-server": True,
                   "x-ssl-identity": (_testpath("server-certificate.pem"),
                                      _testpath("server-private-key.pem"),
                                      server_password)}
        server = self.container1.create_connection("server",
                                                   properties=s_props)

        c_props = {"x-ssl-ca-file": _testpath("ca-certificate.pem"),
                   "x-ssl-verify-mode": "verify-peer",
                   "x-ssl-peer-name": server_dns}
        client = self.container2.create_connection("client",
                                                   properties=c_props)
        server.open()
        client.open()
        common.process_connections(server, client)
        assert server.active and client.active

    def test_ssl_ok(self):
        try:
            self._test_ssl()
        except SSLUnavailable:
            raise common.Skipped("SSL not available.")

    def test_ssl_pw_fail(self):
        try:
            self._test_ssl(server_password="bad-server-password")
            assert False, "error expected!"
        except SSLUnavailable:
            raise common.Skipped("SSL not available.")
        except Exception:
            pass

    def test_ssl_name_fail(self):
        try:
            self._test_ssl(server_dns="bad-name")
            assert False, "error expected!"
        except SSLUnavailable:
            raise common.Skipped("SSL not available.")
        except Exception:
            pass

    def test_io_input_close(self):
        """Premature input close should trigger failed callback."""
        cb1 = common.ConnCallback()
        c1 = self.container1.create_connection("c1", cb1)
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        c1.close_input()
        assert c1.needs_input == dingus.Connection.EOS
        assert cb1.failed_ct == 0
        c1.process(time.time())
        assert cb1.failed_ct > 0
        assert cb1.failed_error

    def test_io_output_close(self):
        """Premature output close should trigger failed callback."""
        cb1 = common.ConnCallback()
        c1 = self.container1.create_connection("c1", cb1)
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        common.process_connections(c1, c2)
        c1.close_output()
        assert c1.has_output == dingus.Connection.EOS
        assert cb1.failed_ct == 0
        c1.process(time.time())
        assert cb1.failed_ct > 0
        assert cb1.failed_error

    def test_process_reentrancy(self):
        """Catch any attempt to re-enter Connection.process() from a
        callback."""
        class BadCallback(common.ConnCallback):
            def connection_active(self, connection):
                # This is illegal:
                connection.process(time.time())

        cb1 = BadCallback()
        c1 = self.container1.create_connection("c1", cb1)
        c2 = self.container2.create_connection("c2")
        c1.open()
        c2.open()
        try:
            common.process_connections(c1, c2)
            assert False, "RuntimeError expected!"
        except RuntimeError:
            pass

    def _test_accept_receiver_sync(self, r_conn_handler,
                                   src_addr, tgt_addr, name):

        s_conn = self.container1.create_connection("s_conn")
        r_conn = self.container2.create_connection("r_conn",
                                                   r_conn_handler)
        sender = s_conn.create_sender(src_addr, tgt_addr, name=name)
        s_conn.open()
        r_conn.open()
        sender.open()
        common.process_connections(s_conn, r_conn)
        return (s_conn, r_conn, sender)

    def test_accept_receiver_sync(self):

        class recv_accept(common.ConnCallback):
            def __init__(self):
                super(recv_accept, self).__init__()
                self.receiver = None

            def receiver_requested(self, connection, link_handle,
                                   name, requested_target,
                                   properties):
                super(recv_accept, self).receiver_requested(connection,
                                                            link_handle,
                                                            name,
                                                            requested_target,
                                                            properties)
                assert name == "my-name"
                assert requested_target == "target-addr"
                assert properties.get("source-address") == "source-addr"
                rx = connection.accept_receiver(link_handle,
                                                target_override="fleabag")
                assert rx
                self.receiver = rx
                self.receiver.open()

        r_cb = recv_accept()
        ret = self._test_accept_receiver_sync(r_cb,
                                              "source-addr", "target-addr",
                                              "my-name")
        assert r_cb.receiver is not None
        # verify the target address was overridden:
        assert r_cb.receiver.target_address == "fleabag"
        assert r_cb.receiver.source_address == "source-addr"
        assert ret[2].target_address == "fleabag"

    def test_accept_receiver_dynamic_sync(self):

        class recv_accept(common.ConnCallback):
            def __init__(self, r_handler):
                super(recv_accept, self).__init__()
                self.receiver = None
                self._handler = r_handler

            def receiver_requested(self, connection, link_handle,
                                   name, requested_target,
                                   properties):
                super(recv_accept, self).receiver_requested(connection,
                                                            link_handle,
                                                            name,
                                                            requested_target,
                                                            properties)
                assert requested_target is None
                props = {"distribution-mode": "copy"}
                rx = connection.accept_receiver(link_handle,
                                                target_override="tgt",
                                                event_handler=self._handler,
                                                properties=props)
                assert rx
                self.receiver = rx
                self.receiver.open()
                self.receiver.add_capacity(10)

        r_handler = common.ReceiverCallback()
        r_cb = recv_accept(r_handler)
        ret = self._test_accept_receiver_sync(r_cb,
                                              "source-addr", None,
                                              "my-name")
        receiver = r_cb.receiver
        assert receiver is not None
        assert receiver.target_address == "tgt"
        assert receiver.capacity == 10
        assert ret[2].target_address == "tgt"
        assert r_handler.active_ct > 0

    def test_accept_receiver_async(self):
        r_handler = common.ConnCallback()
        s_conn = self.container1.create_connection("s_conn")
        r_conn = self.container2.create_connection("r_conn",
                                                   r_handler)
        sender = s_conn.create_sender("src-addr", "a-tgt-addr", None,
                                      "fleabag",
                                      {"distribution-mode": "copy"})
        s_conn.open()
        r_conn.open()
        sender.open()
        common.process_connections(s_conn, r_conn)
        assert r_handler.receiver_requested_ct == 1
        args = r_handler.receiver_requested_args[0]
        assert args.name == "fleabag"
        assert args.requested_source is None
        assert args.requested_target == "a-tgt-addr"
        assert args.properties.get("source-address") == "src-addr"
        assert args.properties.get("distribution-mode") == "copy"
        rx = r_conn.accept_receiver(args.link_handle, "b-tgt-addr")
        rx.open()
        common.process_connections(s_conn, r_conn)
        assert sender.active
        assert sender.target_address == "b-tgt-addr"
        assert rx.active

    def _test_reject_receiver_sync(self, pn_condition):

        class recv_reject(dingus.ConnectionEventHandler):
            def __init__(self, pn_condition):
                self._pn_condition = pn_condition

            def receiver_requested(self, connection, link_handle,
                                   name, requested_target,
                                   properties):
                connection.reject_receiver(link_handle, self._pn_condition)

        s_conn = self.container1.create_connection("s_conn")
        r_cb = recv_reject(pn_condition)
        r_conn = self.container2.create_connection("r_conn", r_cb)
        s_conn.open()
        r_conn.open()

        s_handler = common.SenderCallback()
        sender = s_conn.create_sender("source-addr",
                                      "target-addr",
                                      s_handler,
                                      "my-name")
        sender.open()
        common.process_connections(s_conn, r_conn)
        assert s_handler.remote_closed_ct > 0
        return s_handler.remote_closed_error

    def test_reject_receiver_sync(self):
        cond = Condition("reject", "Smells funny",
                         {"aroma": "bananas"})
        error = self._test_reject_receiver_sync(cond)
        assert error.name == "reject"
        assert error.description == "Smells funny"
        assert error.info["aroma"] == "bananas"

    def test_reject_receiver_no_condition_sync(self):
        error = self._test_reject_receiver_sync(None)
        assert error is None

    def test_reject_receiver_async(self):

        rc_handler = common.ConnCallback()
        s_conn = self.container1.create_connection("s_conn")
        r_conn = self.container2.create_connection("r_conn", rc_handler)
        s_conn.open()
        r_conn.open()

        sl_handler = common.SenderCallback()
        sender = s_conn.create_sender("source-addr",
                                      "target-addr",
                                      sl_handler,
                                      "my-name")
        sender.open()
        common.process_connections(s_conn, r_conn)
        assert sl_handler.remote_closed_ct == 0
        assert rc_handler.receiver_requested_ct == 1
        args = rc_handler.receiver_requested_args[0]
        cond = Condition("reject", "Smells funny",
                         {"aroma": "wet dog"})
        r_conn.reject_receiver(args.link_handle, cond)
        common.process_connections(s_conn, r_conn)
        assert sl_handler.remote_closed_ct == 1
        assert sl_handler.remote_closed_error.name == "reject"

    def _test_accept_sender_sync(self, s_conn_handler,
                                 src_addr, tgt_addr, name):

        s_conn = self.container1.create_connection("s_conn",
                                                   s_conn_handler)
        r_conn = self.container2.create_connection("r_conn")
        receiver = r_conn.create_receiver(tgt_addr, src_addr, name=name)
        s_conn.open()
        r_conn.open()
        receiver.open()
        common.process_connections(s_conn, r_conn)
        return (s_conn, r_conn, receiver)

    def test_accept_sender_sync(self):

        class sender_accept(common.ConnCallback):
            def __init__(self):
                super(sender_accept, self).__init__()
                self.sender = None

            def sender_requested(self, connection, link_handle,
                                 name, requested_source,
                                 properties):
                super(sender_accept, self).sender_requested(connection,
                                                            link_handle,
                                                            name,
                                                            requested_source,
                                                            properties)
                assert name == "my-name"
                assert requested_source == "src-addr"
                assert properties.get("target-address") == "tgt-addr"
                tx = connection.accept_sender(link_handle,
                                              source_override="fleabag")
                assert tx
                self.sender = tx
                self.sender.open()

        s_cb = sender_accept()
        ret = self._test_accept_sender_sync(s_cb, "src-addr", "tgt-addr",
                                            "my-name")
        assert s_cb.sender is not None
        # verify the source address was overridden:
        assert s_cb.sender.source_address == "fleabag"
        assert s_cb.sender.target_address == "tgt-addr"
        assert ret[2].source_address == "fleabag"

    def test_accept_sender_dynamic_sync(self):

        class sender_accept(common.ConnCallback):
            def __init__(self, s_handler):
                super(sender_accept, self).__init__()
                self.sender = None
                self._handler = s_handler

            def sender_requested(self, connection, link_handle,
                                 name, requested_source,
                                 properties):
                super(sender_accept, self).sender_requested(connection,
                                                            link_handle,
                                                            name,
                                                            requested_source,
                                                            properties)
                assert requested_source is None
                props = {"distribution-mode": "copy"}
                tx = connection.accept_sender(link_handle,
                                              source_override="src",
                                              event_handler=self._handler,
                                              properties=props)
                assert tx
                self.sender = tx
                self.sender.open()

        s_handler = common.SenderCallback()
        s_cb = sender_accept(s_handler)
        ret = self._test_accept_sender_sync(s_cb, None, "tgt-addr", "my-name")
        sender = s_cb.sender
        assert sender is not None
        assert sender.target_address == "tgt-addr"
        assert sender.source_address == "src"
        assert ret[2].source_address == "src"
        assert s_handler.active_ct > 0

    def test_accept_sender_async(self):
        sc_handler = common.ConnCallback()
        s_conn = self.container1.create_connection("s_conn",
                                                   sc_handler)
        r_conn = self.container2.create_connection("r_conn")
        rl_handler = common.ReceiverCallback()
        receiver = r_conn.create_receiver("tgt_addr",
                                          "src_addr",
                                          rl_handler,
                                          "name")
        s_conn.open()
        r_conn.open()
        receiver.open()
        common.process_connections(s_conn, r_conn)
        assert rl_handler.remote_closed_ct == 0
        assert sc_handler.sender_requested_ct == 1
        args = sc_handler.sender_requested_args[0]
        assert args.name == "name"
        assert args.requested_source == "src_addr"
        assert args.requested_target is None
        assert args.properties.get("target-address") == "tgt_addr"
        tx = s_conn.accept_sender(args.link_handle,
                                  "real-source")
        tx.open()
        common.process_connections(s_conn, r_conn)
        assert receiver.active
        assert receiver.source_address == "real-source"
        assert tx.active

    def _test_reject_sender_sync(self, pn_condition):

        class send_reject(dingus.ConnectionEventHandler):
            def __init__(self, pn_condition):
                self._pn_condition = pn_condition

            def sender_requested(self, connection, link_handle,
                                 name, requested_source,
                                 properties):
                connection.reject_sender(link_handle, self._pn_condition)

        s_cb = send_reject(pn_condition)
        s_conn = self.container1.create_connection("s_conn", s_cb)
        r_conn = self.container2.create_connection("r_conn")
        s_conn.open()
        r_conn.open()

        r_handler = common.ReceiverCallback()
        rx = r_conn.create_receiver("target-addr",
                                    "source-addr",
                                    r_handler,
                                    "my-name")
        rx.open()
        common.process_connections(s_conn, r_conn)
        assert r_handler.remote_closed_ct > 0
        return r_handler.remote_closed_error

    def test_reject_sender_sync(self):

        cond = Condition("reject", "Smells weird",
                         {"aroma": "fishy"})
        error = self._test_reject_sender_sync(cond)
        assert error.name == "reject"
        assert error.description == "Smells weird"
        assert error.info["aroma"] == "fishy"

    def test_reject_sender_no_condition_sync(self):
        error = self._test_reject_sender_sync(None)
        assert error is None

    def test_reject_sender_async(self):
        sc_handler = common.ConnCallback()
        s_conn = self.container1.create_connection("s_conn",
                                                   sc_handler)
        r_conn = self.container2.create_connection("r_conn")
        rl_handler = common.ReceiverCallback()
        receiver = r_conn.create_receiver("tgt_addr",
                                          "src_addr",
                                          rl_handler,
                                          "name")
        s_conn.open()
        r_conn.open()
        receiver.open()
        common.process_connections(s_conn, r_conn)
        assert rl_handler.remote_closed_ct == 0
        assert sc_handler.sender_requested_ct == 1
        args = sc_handler.sender_requested_args[0]
        cond = Condition("reject", "Smells weird",
                         {"aroma": "eggs"})

        s_conn.reject_sender(args.link_handle, cond)
        common.process_connections(s_conn, r_conn)

        assert rl_handler.remote_closed_ct == 1
        error = rl_handler.remote_closed_error
        assert error.name == "reject"
