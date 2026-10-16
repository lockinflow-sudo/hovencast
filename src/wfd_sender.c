#include <gio/gio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/rtsp-server/rtsp-server.h>
#include <gst/rtsp/gstrtspmessage.h>
#include <gst/rtsp/gstrtspurl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>

typedef enum {
  WFD_NEW,
  WFD_M1_OPTIONS,
  WFD_M2_OPTIONS,
  WFD_M3_GET_PARAMETERS,
  WFD_M4_SET_PARAMETERS,
  WFD_M5_TRIGGER_SETUP,
  WFD_AWAIT_SETUP,
  WFD_STREAMING,
} WfdState;

static GMainLoop *main_loop;
static gboolean receiver_streaming;
static GInetAddress *expected_receiver_address;
static GInetAddressMask *p2p_receiver_network;
static gboolean p2p_peer_connected;
static gboolean p2p_group_owner;
static int exit_status;
static gboolean video_trace_enabled;
static gint64 video_trace_started;
static GMutex video_trace_lock;

static gboolean video_trace_active(gint64 now) {
  g_mutex_lock(&video_trace_lock);
  if (video_trace_enabled && !video_trace_started)
    video_trace_started = now;
  gboolean active = video_trace_enabled && now - video_trace_started < 90 * G_USEC_PER_SEC;
  g_mutex_unlock(&video_trace_lock);
  return active;
}

static gboolean start_live_video_trace(gpointer unused) {
  (void)unused;
  g_mutex_lock(&video_trace_lock);
  video_trace_started = 0;
  video_trace_enabled = TRUE;
  g_mutex_unlock(&video_trace_lock);
  g_print("{\"event\":\"video-trace-started\",\"seconds\":90}\n");
  return G_SOURCE_CONTINUE;
}

static void trace_transport_video(GstBuffer *buffer, const char *stage, gboolean rtp) {
  gint64 now = g_get_monotonic_time();
  if (!video_trace_active(now))
    return;
  GstMapInfo map;
  if (!gst_buffer_map(buffer, &map, GST_MAP_READ))
    return;
  if (rtp && map.size < 12)
    goto done;
  gsize offset = rtp ? 12 + 4 * (map.data[0] & 15) : 0;
  if (offset > map.size)
    goto done;
  if (rtp && (map.data[0] & 0x10)) {
    if (offset + 4 > map.size)
      goto done;
    offset += 4 + 4 * (((guint)map.data[offset + 2] << 8) | map.data[offset + 3]);
  }
  for (; offset + 188 <= map.size; offset += 188) {
    const guint8 *ts = map.data + offset;
    if (ts[0] != 0x47 || !(ts[1] & 0x40) ||
        (((ts[1] & 31) << 8) | ts[2]) != 256 || !(ts[3] & 0x10))
      continue;
    guint header = 4 + ((ts[3] & 0x20) ? 1 + ts[4] : 0);
    if (header + 14 > 188)
      continue;
    const guint8 *pes = ts + header;
    if (pes[0] || pes[1] || pes[2] != 1 || !(pes[7] & 0x80) || pes[8] < 5)
      continue;
    const guint8 *p = pes + 9;
    guint64 pts = ((guint64)(p[0] & 14) << 29) | ((guint64)p[1] << 22) |
                  ((guint64)(p[2] & 254) << 14) | ((guint64)p[3] << 7) | (p[4] >> 1);
    guint payload_start = header + 9 + pes[8];
    g_autofree char *digest = payload_start + 64 <= 188
        ? g_compute_checksum_for_data(G_CHECKSUM_SHA256, ts + payload_start, 64) : NULL;
    g_print("{\"event\":\"video-stage\",\"stage\":\"%s\",\"monotonic_ns\":%"
            G_GINT64_FORMAT ",\"pts90k\":%" G_GUINT64_FORMAT
            ",\"payload_sha256\":%s%s%s}\n", stage, now * 1000, pts,
            digest ? "\"" : "", digest ? digest : "null", digest ? "\"" : "");
  }
done:
  gst_buffer_unmap(buffer, &map);
}

static void trace_rtp_video(GstBuffer *buffer) {
  trace_transport_video(buffer, "rtp-packetized", TRUE);
}

typedef struct {
  const char *stage;
  GByteArray *pending;
} TransportTrace;

static void transport_trace_free(gpointer data) {
  TransportTrace *trace = data;
  g_byte_array_unref(trace->pending);
  g_free(trace);
}

static void trace_transport_chunk(TransportTrace *trace, GstBuffer *buffer) {
  GstMapInfo map;
  if (!gst_buffer_map(buffer, &map, GST_MAP_READ))
    return;
  g_byte_array_append(trace->pending, map.data, map.size);
  gst_buffer_unmap(buffer, &map);
  /* FIFO reads may split a 188-byte TS packet across GstBuffers. */
  gsize complete = trace->pending->len / 188 * 188;
  if (complete) {
    if (video_trace_active(g_get_monotonic_time())) {
      GstBuffer *packets = gst_buffer_new_allocate(NULL, complete, NULL);
      gst_buffer_fill(packets, 0, trace->pending->data, complete);
      trace_transport_video(packets, trace->stage, FALSE);
      gst_buffer_unref(packets);
    }
    g_byte_array_remove_range(trace->pending, 0, complete);
  }
}

static GstPadProbeReturn trace_transport_pad(GstPad *pad, GstPadProbeInfo *info, gpointer data) {
  (void)pad;
  if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
    GstBufferList *list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
    for (guint i = 0; i < gst_buffer_list_length(list); i++)
      trace_transport_chunk(data, gst_buffer_list_get(list, i));
  } else if (GST_PAD_PROBE_INFO_BUFFER(info)) {
    trace_transport_chunk(data, GST_PAD_PROBE_INFO_BUFFER(info));
  }
  return GST_PAD_PROBE_OK;
}

#define TYPE_OMARCHY_WFD_CLIENT (omarchy_wfd_client_get_type())
G_DECLARE_FINAL_TYPE(OmarchyWfdClient, omarchy_wfd_client, OMARCHY, WFD_CLIENT, GstRTSPClient)

struct _OmarchyWfdClient {
  GstRTSPClient parent_instance;
  WfdState state;
  guint keep_alive_source_id;
  gboolean authorized_peer;
};

G_DEFINE_TYPE(OmarchyWfdClient, omarchy_wfd_client, GST_TYPE_RTSP_CLIENT)

static void event(const char *name, const char *detail) {
  g_autofree char *escaped = g_strescape(detail ? detail : "", NULL);
  g_print("{\"event\":\"%s\",\"detail\":\"%s\"}\n", name, escaped);
}

static GstRTSPResult send_request(OmarchyWfdClient *self, GstRTSPMethod method,
                                  const char *uri, const char *content_type,
                                  const char *body) {
  GstRTSPMessage message = {0};
  GstRTSPResult result;
  gst_rtsp_message_init_request(&message, method, uri);
  if (content_type)
    gst_rtsp_message_add_header_by_name(&message, "Content-Type", content_type);
  if (body)
    gst_rtsp_message_set_body(&message, (guint8 *)body, strlen(body));
  result = gst_rtsp_client_send_message(GST_RTSP_CLIENT(self), NULL, &message);
  gst_rtsp_message_unset(&message);
  return result;
}

static gboolean query_parameters(gpointer data) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(data);
  const char *query =
      "wfd_client_rtp_ports\r\n"
      "wfd_audio_codecs\r\n"
      "wfd_video_formats\r\n"
      "wfd_display_edid\r\n"
      "wfd_idr_request_capability\r\n"
      "microsoft_cursor\r\n";
  self->state = WFD_M3_GET_PARAMETERS;
  send_request(self, GST_RTSP_GET_PARAMETER, "rtsp://localhost/wfd1.0",
               "text/parameters", query);
  event("rtsp-send", "M3 GET_PARAMETER");
  g_object_unref(self);
  return G_SOURCE_REMOVE;
}

static void parse_rtp_ports(const char *body, guint *rtp, guint *rtcp) {
  const char *line = strstr(body, "wfd_client_rtp_ports:");
  *rtp = 16384;
  *rtcp = 16385;
  if (line) {
    const char *unicast = strstr(line, "unicast ");
    if (unicast && sscanf(unicast + 8, "%u %u", rtp, rtcp) == 2) {
      if (*rtcp == 0 || *rtcp == *rtp)
        *rtcp = *rtp + 1;
    }
  }
}

static char *local_rtsp_uri(OmarchyWfdClient *self) {
  GstRTSPConnection *connection = gst_rtsp_client_get_connection(GST_RTSP_CLIENT(self));
  GSocket *socket = gst_rtsp_connection_get_read_socket(connection);
  g_autoptr(GSocketAddress) socket_address = g_socket_get_local_address(socket, NULL);
  GInetAddress *address = g_inet_socket_address_get_address(G_INET_SOCKET_ADDRESS(socket_address));
  g_autofree char *address_text = g_inet_address_to_string(address);
  return g_strdup_printf("rtsp://%s:7236/wfd1.0/streamid=0", address_text);
}

static char *select_video_format(const char *parameters) {
  const char *cea_mode = g_getenv("OMARCHY_CAST_CEA_MODE");
  if (!cea_mode || strlen(cea_mode) != 8 ||
      strspn(cea_mode, "0123456789abcdefABCDEF") != 8)
    cea_mode = "00000080";
  const char *start = strstr(parameters, "wfd_video_formats:");
  if (!start)
    return g_strdup_printf("00 00 01 01 %s 00000000 00000000 00 0000 0000 00 none none", cea_mode);
  start = strchr(start, ':') + 1;
  const char *end = strstr(start, "\r\n");
  g_autofree char *line = g_strndup(start, end ? (gsize)(end - start) : strlen(start));
  g_auto(GStrv) fields = g_strsplit(g_strstrip(line), " ", 3);
  if (g_strv_length(fields) != 3)
    return g_strdup_printf("00 00 01 01 %s 00000000 00000000 00 0000 0000 00 none none", cea_mode);
  g_auto(GStrv) codecs = g_strsplit(fields[2], ",", -1);
  char *selected = codecs[0];
  for (char **codec = codecs; codec && *codec; codec++) {
    guint profile = (guint)g_ascii_strtoull(g_strstrip(*codec), NULL, 16);
    if (profile & 0x01) {
      selected = *codec;
      break;
    }
  }
  g_auto(GStrv) tokens = g_strsplit(g_strstrip(selected), " ", -1);
  if (g_strv_length(tokens) < 9)
    return g_strdup_printf("00 00 01 01 %s 00000000 00000000 00 0000 0000 00 none none", cea_mode);
  guint profile = (guint)g_ascii_strtoull(tokens[0], NULL, 16);
  guint level = (guint)g_ascii_strtoull(tokens[1], NULL, 16);
  guint latency = (guint)g_ascii_strtoull(tokens[5], NULL, 16);
  guint min_slice = (guint)g_ascii_strtoull(tokens[6], NULL, 16);
  guint frame_control = (guint)g_ascii_strtoull(tokens[8], NULL, 16) & 0x01;
  return g_strdup_printf(
      "00 00 %02X %02X %s 00000000 00000000 %02X %04X 0000 %02X none none",
      profile, level, cea_mode, latency, min_slice, frame_control);
}

static gboolean set_parameters_idle(gpointer data) {
  struct Parameters {
    OmarchyWfdClient *client;
    char *body;
  } *parameters = data;
  OmarchyWfdClient *self = parameters->client;
  guint rtp, rtcp;
  parse_rtp_ports(parameters->body, &rtp, &rtcp);
  g_autofree char *uri = local_rtsp_uri(self);
  g_autofree char *video_format = select_video_format(parameters->body);
  g_autofree char *body = g_strdup_printf(
      "wfd_video_formats: %s\r\n"
      "wfd_audio_codecs: AAC 00000001 00\r\n"
      "wfd_presentation_URL: %s none\r\n"
      "wfd_client_rtp_ports: RTP/AVP/UDP;unicast %u %u mode=play\r\n",
      video_format, uri, rtp, rtcp);
  event("video-format-selected", video_format);
  self->state = WFD_M4_SET_PARAMETERS;
  send_request(self, GST_RTSP_SET_PARAMETER, "rtsp://localhost/wfd1.0",
               "text/parameters", body);
  event("rtsp-send", "M4 SET_PARAMETER");
  g_free(parameters->body);
  g_object_unref(self);
  g_free(parameters);
  return G_SOURCE_REMOVE;
}

static gboolean trigger_setup(gpointer data) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(data);
  self->state = WFD_M5_TRIGGER_SETUP;
  send_request(self, GST_RTSP_SET_PARAMETER, "rtsp://localhost/wfd1.0",
               "text/parameters", "wfd_trigger_method: SETUP\r\n");
  event("rtsp-send", "M5 trigger SETUP");
  g_object_unref(self);
  return G_SOURCE_REMOVE;
}

static void handle_response(GstRTSPClient *client, GstRTSPContext *ctx) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(client);
  g_autofree char *body = NULL;
  guint8 *response_body = NULL;
  guint response_size = 0;
  /* The public accessor also handles messages backed by a GstBuffer. */
  if (gst_rtsp_message_get_body(ctx->response, &response_body, &response_size) == GST_RTSP_OK
      && response_body && response_size)
    body = g_strndup((char *)response_body, response_size);
  event("rtsp-response", body ? body : "");
  switch (self->state) {
    case WFD_M1_OPTIONS:
      self->state = WFD_M2_OPTIONS;
      break;
    case WFD_M3_GET_PARAMETERS: {
      struct Parameters {
        OmarchyWfdClient *client;
        char *body;
      } *parameters = g_new0(struct Parameters, 1);
      parameters->client = g_object_ref(self);
      parameters->body = g_strdup(body ? body : "");
      g_idle_add(set_parameters_idle, parameters);
      break;
    }
    case WFD_M4_SET_PARAMETERS:
      g_idle_add(trigger_setup, g_object_ref(self));
      break;
    case WFD_M5_TRIGGER_SETUP:
      self->state = WFD_AWAIT_SETUP;
      break;
    default:
      break;
  }
}

static GstRTSPStatusCode pre_options_request(GstRTSPClient *client, GstRTSPContext *ctx) {
  (void)ctx;
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(client);
  event("rtsp-receive", "M2 OPTIONS");
  if (self->state == WFD_M2_OPTIONS)
    g_idle_add(query_parameters, g_object_ref(self));
  return GST_RTSP_STS_OK;
}

static gchar *check_requirements(GstRTSPClient *client, GstRTSPContext *ctx, gchar **requirements) {
  (void)client;
  (void)ctx;
  g_autoptr(GPtrArray) unsupported = g_ptr_array_new();
  for (gchar **item = requirements; item && *item; item++)
    if (!g_str_equal(*item, "org.wfa.wfd1.0"))
      g_ptr_array_add(unsupported, *item);
  g_ptr_array_add(unsupported, NULL);
  return g_strjoinv(", ", (gchar **)unsupported->pdata);
}

static GstRTSPResult params_set(GstRTSPClient *client, GstRTSPContext *ctx) {
  (void)client;
  gst_rtsp_message_init_response(ctx->response, GST_RTSP_STS_OK,
                                 gst_rtsp_status_as_text(GST_RTSP_STS_OK), ctx->request);
  event("rtsp-receive", "SET_PARAMETER");
  return GST_RTSP_OK;
}

static GstRTSPStatusCode pre_play_request(GstRTSPClient *client, GstRTSPContext *ctx) {
  (void)ctx;
  OMARCHY_WFD_CLIENT(client)->state = WFD_STREAMING;
  receiver_streaming = TRUE;
  event("receiver-state", "streaming");
  return GST_RTSP_STS_OK;
}

static GstRTSPStatusCode pre_setup_request(GstRTSPClient *client, GstRTSPContext *ctx) {
  (void)client;
  const GstRTSPUrl *uri = ctx->uri;
  g_autofree char *detail = uri
      ? g_strdup_printf("SETUP %s", uri->abspath ? uri->abspath : "")
      : g_strdup("SETUP");
  event("rtsp-receive", detail);
  return GST_RTSP_STS_OK;
}

static GstRTSPStatusCode pre_teardown_request(GstRTSPClient *client,
                                              GstRTSPContext *ctx) {
  (void)client;
  (void)ctx;
  event("rtsp-receive", "TEARDOWN");
  return GST_RTSP_STS_OK;
}

static void send_message(GstRTSPClient *client, GstRTSPContext *ctx,
                         GstRTSPMessage *message) {
  (void)client;
  (void)ctx;
  gchar *public_header = NULL;
  if (gst_rtsp_message_get_header(message, GST_RTSP_HDR_PUBLIC,
                                  &public_header, 0) == GST_RTSP_OK &&
      public_header && !strstr(public_header, "org.wfa.wfd1.0")) {
    g_autofree char *with_wfd = g_strconcat("org.wfa.wfd1.0, ", public_header, NULL);
    gst_rtsp_message_remove_header(message, GST_RTSP_HDR_PUBLIC, -1);
    gst_rtsp_message_add_header(message, GST_RTSP_HDR_PUBLIC, with_wfd);
    event("rtsp-advertise", with_wfd);
  }
}

static GstRTSPFilterResult keep_alive_session(GstRTSPClient *client,
                                              GstRTSPSession *session,
                                              gpointer user_data) {
  (void)user_data;
  GstRTSPMessage message = {0};
  gst_rtsp_session_touch(session);
  gst_rtsp_message_init_request(&message, GST_RTSP_GET_PARAMETER,
                                "rtsp://localhost/wfd1.0");
  GstRTSPResult result = gst_rtsp_client_send_message(client, session, &message);
  gst_rtsp_message_unset(&message);
  event("rtsp-keepalive", result == GST_RTSP_OK ? "sent" : "send-failed");
  return GST_RTSP_FILTER_KEEP;
}

static gboolean keep_alive_timeout(gpointer data) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(data);
  if (!gst_rtsp_client_get_connection(GST_RTSP_CLIENT(self))) {
    self->keep_alive_source_id = 0;
    return G_SOURCE_REMOVE;
  }
  GList *sessions = gst_rtsp_client_session_filter(GST_RTSP_CLIENT(self),
                                                   keep_alive_session, NULL);
  g_list_free(sessions);
  return G_SOURCE_CONTINUE;
}

static void new_session(GstRTSPClient *client, GstRTSPSession *session) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(client);
  gst_rtsp_session_set_timeout(session, 300);
  g_object_set(session, "timeout-always-visible", FALSE, NULL);
  if (!self->keep_alive_source_id)
    self->keep_alive_source_id = g_timeout_add_seconds(15, keep_alive_timeout, self);
}

static void client_closed(GstRTSPClient *client) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(client);
  if (self->keep_alive_source_id) {
    g_source_remove(self->keep_alive_source_id);
    self->keep_alive_source_id = 0;
  }
  event("receiver-state", self->authorized_peer ? "rtsp-closed" : "rejected-peer-closed");
  if (self->authorized_peer && main_loop)
    g_main_loop_quit(main_loop);
}

static gchar *make_path_from_uri(GstRTSPClient *client, const GstRTSPUrl *uri) {
  GstRTSPContext *ctx = gst_rtsp_context_get_current();
  if (ctx && ctx->request &&
      (ctx->request->type_data.request.method == GST_RTSP_PLAY ||
       ctx->request->type_data.request.method == GST_RTSP_PAUSE) &&
      g_str_has_suffix(uri->abspath, "/streamid=0"))
    return g_strndup(uri->abspath, strlen(uri->abspath) - 11);
  return GST_RTSP_CLIENT_CLASS(omarchy_wfd_client_parent_class)->make_path_from_uri(client, uri);
}

static void omarchy_wfd_client_finalize(GObject *object) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(object);
  if (self->keep_alive_source_id) {
    g_source_remove(self->keep_alive_source_id);
    self->keep_alive_source_id = 0;
  }
  G_OBJECT_CLASS(omarchy_wfd_client_parent_class)->finalize(object);
}

static void omarchy_wfd_client_class_init(OmarchyWfdClientClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GstRTSPClientClass *client_class = GST_RTSP_CLIENT_CLASS(klass);
  client_class->handle_response = handle_response;
  client_class->pre_options_request = pre_options_request;
  client_class->check_requirements = check_requirements;
  client_class->params_set = params_set;
  client_class->pre_setup_request = pre_setup_request;
  client_class->pre_play_request = pre_play_request;
  client_class->pre_teardown_request = pre_teardown_request;
  client_class->make_path_from_uri = make_path_from_uri;
  client_class->send_message = send_message;
  client_class->new_session = new_session;
  client_class->closed = client_closed;
  object_class->finalize = omarchy_wfd_client_finalize;
}

static void omarchy_wfd_client_init(OmarchyWfdClient *self) { self->state = WFD_NEW; }

#define TYPE_OMARCHY_WFD_SERVER (omarchy_wfd_server_get_type())
G_DECLARE_FINAL_TYPE(OmarchyWfdServer, omarchy_wfd_server, OMARCHY, WFD_SERVER, GstRTSPServer)

struct _OmarchyWfdServer { GstRTSPServer parent_instance; };
G_DEFINE_TYPE(OmarchyWfdServer, omarchy_wfd_server, GST_TYPE_RTSP_SERVER)

static GstRTSPClient *create_client(GstRTSPServer *server) {
  GstRTSPClient *client = g_object_new(TYPE_OMARCHY_WFD_CLIENT, NULL);
  g_autoptr(GstRTSPSessionPool) sessions = gst_rtsp_server_get_session_pool(server);
  g_autoptr(GstRTSPMountPoints) mounts = gst_rtsp_server_get_mount_points(server);
  g_autoptr(GstRTSPAuth) auth = gst_rtsp_server_get_auth(server);
  g_autoptr(GstRTSPThreadPool) threads = gst_rtsp_server_get_thread_pool(server);
  gst_rtsp_client_set_session_pool(client, sessions);
  gst_rtsp_client_set_mount_points(client, mounts);
  gst_rtsp_client_set_auth(client, auth);
  gst_rtsp_client_set_thread_pool(client, threads);
  return client;
}

static gboolean query_support_idle(gpointer data) {
  OmarchyWfdClient *self = OMARCHY_WFD_CLIENT(data);
  GstRTSPMessage message = {0};
  self->state = WFD_M1_OPTIONS;
  gst_rtsp_message_init_request(&message, GST_RTSP_OPTIONS, "*");
  gst_rtsp_message_add_header_by_name(&message, "Require", "org.wfa.wfd1.0");
  gst_rtsp_client_send_message(GST_RTSP_CLIENT(self), NULL, &message);
  gst_rtsp_message_unset(&message);
  event("rtsp-send", "M1 OPTIONS");
  g_object_unref(self);
  return G_SOURCE_REMOVE;
}

static void client_connected(GstRTSPServer *server, GstRTSPClient *client) {
  (void)server;
  GstRTSPConnection *connection = gst_rtsp_client_get_connection(client);
  GSocket *socket = connection ? gst_rtsp_connection_get_read_socket(connection) : NULL;
  g_autoptr(GSocketAddress) remote =
      socket ? g_socket_get_remote_address(socket, NULL) : NULL;
  GInetAddress *remote_address = remote && G_IS_INET_SOCKET_ADDRESS(remote)
      ? g_inet_socket_address_get_address(G_INET_SOCKET_ADDRESS(remote))
      : NULL;
  gboolean permitted = remote_address && expected_receiver_address &&
      g_inet_address_equal(remote_address, expected_receiver_address);
  /* When we own the P2P group, the sink receives a DHCP address. Accept only
   * the first peer on that dedicated group subnet, then pin its address.
   * The listener is bound to the group's local address, never the LAN. */
  if (remote_address && p2p_receiver_network && !p2p_peer_connected &&
      p2p_group_owner &&
      g_inet_address_mask_matches(p2p_receiver_network, remote_address)) {
    g_set_object(&expected_receiver_address, remote_address);
    permitted = TRUE;
  }
  if (!permitted || (p2p_receiver_network && p2p_peer_connected)) {
    g_autofree char *peer = remote_address
        ? g_inet_address_to_string(remote_address)
        : g_strdup("unknown");
    event("rtsp-rejected-peer", peer);
    gst_rtsp_client_close(client);
    return;
  }
  OMARCHY_WFD_CLIENT(client)->authorized_peer = TRUE;
  if (p2p_receiver_network)
    p2p_peer_connected = TRUE;
  event("receiver-state", "rtsp-connected");
  /* Roku establishes TCP before its RTSP state machine is ready. */
  g_timeout_add(500, query_support_idle, g_object_ref(client));
}

static void omarchy_wfd_server_class_init(OmarchyWfdServerClass *klass) {
  GstRTSPServerClass *server_class = GST_RTSP_SERVER_CLASS(klass);
  server_class->create_client = create_client;
  server_class->client_connected = client_connected;
}

static void omarchy_wfd_server_init(OmarchyWfdServer *self) { (void)self; }

static gboolean write_mice_message(GSocketConnection *connection, guint8 command,
                                   const char *name, const char *source_id,
                                   gboolean include_rtsp_port, GError **error) {
  if (strlen(source_id) != 16) {
    g_set_error(error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT,
                "MICE source ID must be exactly 16 bytes");
    return FALSE;
  }
  g_autofree gunichar2 *utf16 = NULL;
  g_autofree char *with_bom = g_strconcat("\xEF\xBB\xBF", name, NULL);
  glong units = 0;
  utf16 = g_utf8_to_utf16(with_bom, -1, NULL, &units, error);
  if (!utf16)
    return FALSE;
  gsize name_bytes = (gsize)units * 2;
  gsize total = 7 + name_bytes + (include_rtsp_port ? 5 : 0) + 3 + 16;
  if (total > G_MAXUINT16) {
    g_set_error(error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT,
                "MICE message is too large");
    return FALSE;
  }
  g_autofree guint8 *message = g_malloc0(total);
  message[0] = total >> 8;
  message[1] = total & 0xff;
  message[2] = 1;
  message[3] = command;
  message[4] = 0;
  message[5] = name_bytes >> 8;
  message[6] = name_bytes & 0xff;
  memcpy(message + 7, utf16, name_bytes);
  gsize offset = 7 + name_bytes;
  if (include_rtsp_port) {
    const guint8 rtsp_port[] = {2, 0, 2, 0x1c, 0x44};
    memcpy(message + offset, rtsp_port, sizeof(rtsp_port));
    offset += sizeof(rtsp_port);
  }
  const guint8 source_id_header[] = {3, 0, 16};
  memcpy(message + offset, source_id_header, sizeof(source_id_header));
  offset += sizeof(source_id_header);
  memcpy(message + offset, source_id, 16);
  GOutputStream *output = g_io_stream_get_output_stream(G_IO_STREAM(connection));
  if (!g_output_stream_write_all(output, message, total, NULL, NULL, error))
    return FALSE;
  return g_output_stream_flush(output, NULL, error);
}

static gboolean send_source_ready(const char *receiver, const char *name,
                                  const char *source_id,
                                  GSocketConnection **out_connection,
                                  GError **error) {
  g_autoptr(GSocketClient) client = g_socket_client_new();
  g_autoptr(GSocketConnection) connection =
      g_socket_client_connect_to_host(client, receiver, 7250, NULL, error);
  if (!connection)
    return FALSE;
  if (!write_mice_message(connection, 0x01, name, source_id, TRUE, error))
    return FALSE;
  *out_connection = g_steal_pointer(&connection);
  return TRUE;
}

static gboolean send_stop_projection(GSocketConnection *connection,
                                     const char *name, const char *source_id,
                                     GError **error) {
  return write_mice_message(connection, 0x02, name, source_id, FALSE, error);
}

static char *route_address_for_receiver(const char *receiver, GError **error) {
  g_autoptr(GInetAddress) remote_address = g_inet_address_new_from_string(receiver);
  if (!remote_address) {
    g_set_error(error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT,
                "receiver address must be a numeric IP address");
    return NULL;
  }
  GSocketFamily family = g_inet_address_get_family(remote_address);
  g_autoptr(GSocket) socket =
      g_socket_new(family, G_SOCKET_TYPE_DATAGRAM, G_SOCKET_PROTOCOL_UDP, error);
  if (!socket)
    return NULL;
  g_autoptr(GSocketAddress) destination =
      g_inet_socket_address_new(remote_address, 7250);
  if (!g_socket_connect(socket, destination, NULL, error))
    return NULL;
  g_autoptr(GSocketAddress) local = g_socket_get_local_address(socket, error);
  if (!local || !G_IS_INET_SOCKET_ADDRESS(local)) {
    if (local)
      g_set_error(error, G_IO_ERROR, G_IO_ERROR_FAILED,
                  "could not determine the receiver-facing local address");
    return NULL;
  }
  GInetAddress *local_address =
      g_inet_socket_address_get_address(G_INET_SOCKET_ADDRESS(local));
  return g_inet_address_to_string(local_address);
}

static gboolean stop_loop(gpointer data) {
  (void)data;
  event("receiver-state", "session-timeout");
  g_main_loop_quit(main_loop);
  return G_SOURCE_REMOVE;
}

static gboolean receiver_start_timeout(gpointer data) {
  (void)data;
  if (!receiver_streaming) {
    event("receiver-state", "connection-timeout");
    exit_status = 1;
    if (main_loop)
      g_main_loop_quit(main_loop);
  }
  return G_SOURCE_REMOVE;
}

static gboolean interrupted(gpointer data) {
  (void)data;
  event("receiver-state", "source-stopped");
  if (main_loop)
    g_main_loop_quit(main_loop);
  return G_SOURCE_REMOVE;
}

typedef struct {
  gint64 started;
  gint64 last_report;
  GstClockTime first_pts;
  guint64 packets;
} RtpProgress;

static GstPadProbeReturn rtp_progress(GstPad *pad, GstPadProbeInfo *info, gpointer data) {
  (void)pad;
  RtpProgress *progress = data;
  GstBuffer *buffer = NULL;
  guint count = 1;
  if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
    GstBufferList *list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
    count = gst_buffer_list_length(list);
    if (count)
      buffer = gst_buffer_list_get(list, count - 1);
    for (guint i = 0; i < count; i++)
      trace_rtp_video(gst_buffer_list_get(list, i));
  } else {
    buffer = GST_PAD_PROBE_INFO_BUFFER(info);
    if (buffer)
      trace_rtp_video(buffer);
  }
  if (!buffer)
    return GST_PAD_PROBE_OK;
  gint64 now = g_get_monotonic_time();
  if (!progress->started) {
    progress->started = now;
    progress->first_pts = GST_BUFFER_PTS(buffer);
  }
  progress->packets += count;
  if (now - progress->last_report >= G_USEC_PER_SEC) {
    progress->last_report = now;
    double media_ms = GST_CLOCK_TIME_IS_VALID(progress->first_pts) && GST_BUFFER_PTS_IS_VALID(buffer)
        ? (double)(GST_BUFFER_PTS(buffer) - progress->first_pts) / GST_MSECOND : -1;
    g_autofree char *detail = g_strdup_printf("packets=%" G_GUINT64_FORMAT " elapsed_ms=%.1f media_ms=%.1f",
        progress->packets, (double)(now - progress->started) / 1000, media_ms);
    event("rtp-progress", detail);
  }
  return GST_PAD_PROBE_OK;
}

static void media_configure(GstRTSPMediaFactory *factory, GstRTSPMedia *media,
                            gpointer user_data) {
  (void)factory;
  (void)user_data;
  {
    GstElement *element = gst_rtsp_media_get_element(media);
    {
      const char *names[] = {"transport_input", "transport_parser"};
      const char *stages[] = {"remux-fifo-input", "tsparse-output"};
      for (guint i = 0; i < G_N_ELEMENTS(names); i++) {
        GstElement *checkpoint = gst_bin_get_by_name_recurse_up(GST_BIN(element), names[i]);
        if (checkpoint) {
          GstPad *checkpoint_pad = gst_element_get_static_pad(checkpoint, "src");
          TransportTrace *trace = g_new0(TransportTrace, 1);
          trace->stage = stages[i];
          trace->pending = g_byte_array_new();
          gst_pad_add_probe(checkpoint_pad, GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST,
              trace_transport_pad, trace, transport_trace_free);
          gst_object_unref(checkpoint_pad);
          gst_object_unref(checkpoint);
        }
      }
    }
    GstElement *pay = gst_bin_get_by_name_recurse_up(GST_BIN(element), "pay0");
    if (pay) {
      GstPad *pad = gst_element_get_static_pad(pay, "src");
      gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST,
          rtp_progress, g_new0(RtpProgress, 1), g_free);
      gst_object_unref(pad);
      gst_object_unref(pay);
    }
    gst_object_unref(element);
  }
  GstRTSPStream *stream = gst_rtsp_media_get_stream(media, 0);
  if (!stream) {
    event("rtsp-media", "missing RTP stream 0");
    return;
  }
  gst_rtsp_stream_set_control(stream, "streamid=0");
  event("rtsp-media", "control=streamid=0");
}

int main(int argc, char **argv) {
  if (argc != 4) {
    g_printerr("usage: %s RECEIVER ARTIFACT TIMEOUT_SECONDS\n", argv[0]);
    return 2;
  }
  gst_init(&argc, &argv);
  video_trace_enabled = g_strcmp0(g_getenv("HOVENCAST_VIDEO_TRACE"), "1") == 0;
  const char *receiver = argv[1];
  const char *artifact = argv[2];
  guint timeout = (guint)g_ascii_strtoull(argv[3], NULL, 10);
  const char *friendly_name = g_getenv("OMARCHY_CAST_FRIENDLY_NAME");
  const char *source_id = g_getenv("OMARCHY_CAST_SOURCE_ID");
  gboolean p2p = g_strcmp0(g_getenv("HOVENCAST_TRANSPORT"), "miracast-p2p") == 0;
  if (!friendly_name || !*friendly_name)
    friendly_name = "Omarchy";
  if (!source_id || !*source_id)
    source_id = "HovenCastSender1";
  g_autoptr(GError) error = NULL;
  g_autofree char *bind_address = p2p
      ? g_strdup(g_getenv("HOVENCAST_P2P_LOCAL_ADDRESS"))
      : route_address_for_receiver(receiver, &error);
  if (p2p) {
    g_autoptr(GInetAddress) local = bind_address
        ? g_inet_address_new_from_string(bind_address) : NULL;
    const char *network = g_getenv("HOVENCAST_P2P_NETWORK");
    if (network)
      p2p_receiver_network = g_inet_address_mask_new_from_string(network, &error);
    if (!local || !p2p_receiver_network ||
        !g_inet_address_mask_matches(p2p_receiver_network, local)) {
      g_printerr("invalid Wi-Fi Direct group address or subnet\n");
      g_clear_object(&p2p_receiver_network);
      return 1;
    }
  }
  if (!bind_address) {
    g_printerr("could not determine safe RTSP bind address: %s\n", error->message);
    return 1;
  }
  expected_receiver_address = g_inet_address_new_from_string(receiver);
  p2p_group_owner = p2p && g_strcmp0(receiver, bind_address) == 0;
  g_autoptr(OmarchyWfdServer) server = g_object_new(TYPE_OMARCHY_WFD_SERVER, NULL);
  gst_rtsp_server_set_address(GST_RTSP_SERVER(server), bind_address);
  gst_rtsp_server_set_service(GST_RTSP_SERVER(server), "7236");
  g_autoptr(GstRTSPMediaFactory) factory = gst_rtsp_media_factory_new();
  g_autofree char *escaped = g_strescape(artifact, NULL);
  g_autofree char *launch = g_strdup_printf(
      "( filesrc name=transport_input location=\"%s\" blocksize=1316 do-timestamp=%s "
      "! tsparse name=transport_parser set-timestamps=%s alignment=7 "
      "! rtpmp2tpay name=pay0 pt=33 )",
      escaped, p2p ? "false" : "true", p2p ? "true" : "false");
  gst_rtsp_media_factory_set_launch(factory, launch);
  g_signal_connect(factory, "media-configure", G_CALLBACK(media_configure), GUINT_TO_POINTER(p2p));
  GstRTSPMountPoints *mounts = gst_rtsp_server_get_mount_points(GST_RTSP_SERVER(server));
  gst_rtsp_mount_points_add_factory(mounts, "/wfd1.0", g_object_ref(factory));
  g_object_unref(mounts);
  if (!gst_rtsp_server_attach(GST_RTSP_SERVER(server), NULL)) {
    g_printerr("could not listen on %s:7236\n", bind_address);
    g_clear_object(&expected_receiver_address);
    return 1;
  }
  g_autoptr(GSocketConnection) mice_connection = NULL;
  main_loop = g_main_loop_new(NULL, FALSE);
  g_unix_signal_add(SIGINT, interrupted, NULL);
  g_unix_signal_add(SIGTERM, interrupted, NULL);
  g_unix_signal_add(SIGUSR1, start_live_video_trace, NULL);
  if (!p2p && !send_source_ready(receiver, friendly_name, source_id, &mice_connection, &error)) {
    g_printerr("MICE signalling failed: %s\n", error->message);
    g_clear_object(&expected_receiver_address);
    g_main_loop_unref(main_loop);
    main_loop = NULL;
    return 1;
  }
  event("rtsp-listen", bind_address);
  event(p2p ? "p2p-source-ready" : "mice-source-ready", receiver);
  g_timeout_add_seconds(15, receiver_start_timeout, NULL);
  if (timeout > 0)
    g_timeout_add_seconds(timeout, stop_loop, NULL);
  g_main_loop_run(main_loop);
  g_autoptr(GError) stop_error = NULL;
  if (!p2p && send_stop_projection(mice_connection, friendly_name, source_id, &stop_error)) {
    event("mice-stop-projection", "sent");
  } else if (!p2p) {
    event("mice-stop-projection", stop_error ? stop_error->message : "send-failed");
  }
  if (mice_connection)
    g_io_stream_close(G_IO_STREAM(mice_connection), NULL, NULL);
  g_main_loop_unref(main_loop);
  main_loop = NULL;
  g_clear_object(&expected_receiver_address);
  g_clear_object(&p2p_receiver_network);
  return exit_status;
}
