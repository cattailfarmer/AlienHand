#include "alienhand_telemetry.h"

#include <godot_cpp/classes/json.hpp>
#include <godot_cpp/classes/time.hpp>
#include <godot_cpp/core/class_db.hpp>

namespace godot {

AlienHandTelemetry::~AlienHandTelemetry() {
    stop_recording();
}

void AlienHandTelemetry::_bind_methods() {
    ClassDB::bind_method(D_METHOD("start_recording", "path", "session_id"), &AlienHandTelemetry::start_recording, DEFVAL("godot"));
    ClassDB::bind_method(D_METHOD("stop_recording"), &AlienHandTelemetry::stop_recording);
    ClassDB::bind_method(D_METHOD("is_recording"), &AlienHandTelemetry::is_recording);
    ClassDB::bind_method(D_METHOD("set_session_id", "session_id"), &AlienHandTelemetry::set_session_id);
    ClassDB::bind_method(D_METHOD("get_session_id"), &AlienHandTelemetry::get_session_id);
    ClassDB::bind_method(D_METHOD("get_log_path"), &AlienHandTelemetry::get_log_path);
    ClassDB::bind_method(D_METHOD("get_frame_count"), &AlienHandTelemetry::get_frame_count);
    ClassDB::bind_method(D_METHOD("make_frame_event", "state"), &AlienHandTelemetry::make_frame_event);
    ClassDB::bind_method(D_METHOD("make_event", "kind", "payload"), &AlienHandTelemetry::make_event, DEFVAL(Dictionary()));
    ClassDB::bind_method(D_METHOD("record_frame", "state"), &AlienHandTelemetry::record_frame);
    ClassDB::bind_method(D_METHOD("log_event", "kind", "payload"), &AlienHandTelemetry::log_event, DEFVAL(Dictionary()));
}

Error AlienHandTelemetry::start_recording(const String &path, const String &session) {
    stop_recording();

    log_file = FileAccess::open(path, FileAccess::WRITE);
    if (log_file.is_null()) {
        return ERR_CANT_OPEN;
    }

    log_path = path;
    session_id = session;
    frame_count = 0;

    Dictionary payload;
    payload["session_id"] = session_id;
    payload["path"] = log_path;
    return log_event("recording_started", payload);
}

void AlienHandTelemetry::stop_recording() {
    if (log_file.is_valid()) {
        Dictionary payload;
        payload["session_id"] = session_id;
        payload["frames"] = frame_count;
        write_jsonl(make_event("recording_stopped", payload));
        log_file->flush();
        log_file.unref();
    }
}

bool AlienHandTelemetry::is_recording() const {
    return log_file.is_valid();
}

void AlienHandTelemetry::set_session_id(const String &session) {
    session_id = session;
}

String AlienHandTelemetry::get_session_id() const {
    return session_id;
}

String AlienHandTelemetry::get_log_path() const {
    return log_path;
}

int AlienHandTelemetry::get_frame_count() const {
    return frame_count;
}

Dictionary AlienHandTelemetry::make_frame_event(const Dictionary &state) const {
    Dictionary event;
    event["type"] = "godot_frame";
    event["session_id"] = session_id;
    event["frame_index"] = frame_count + 1;
    event["ticks_msec"] = Time::get_singleton()->get_ticks_msec();
    event["unix_time"] = Time::get_singleton()->get_unix_time_from_system();
    event["state"] = state;
    return event;
}

Dictionary AlienHandTelemetry::make_event(const String &kind, const Dictionary &payload) const {
    Dictionary event;
    event["type"] = "godot_event";
    event["kind"] = kind;
    event["session_id"] = session_id;
    event["frame_index"] = frame_count;
    event["ticks_msec"] = Time::get_singleton()->get_ticks_msec();
    event["unix_time"] = Time::get_singleton()->get_unix_time_from_system();
    event["payload"] = payload;
    return event;
}

Error AlienHandTelemetry::record_frame(const Dictionary &state) {
    if (log_file.is_null()) {
        return ERR_UNCONFIGURED;
    }

    const Error error = write_jsonl(make_frame_event(state));
    if (error == OK) {
        frame_count++;
    }
    return error;
}

Error AlienHandTelemetry::log_event(const String &kind, const Dictionary &payload) {
    if (log_file.is_null()) {
        return ERR_UNCONFIGURED;
    }
    return write_jsonl(make_event(kind, payload));
}

Error AlienHandTelemetry::write_jsonl(const Dictionary &event) {
    if (log_file.is_null()) {
        return ERR_UNCONFIGURED;
    }

    const String line = JSON::stringify(event, "", true, true);
    if (!log_file->store_line(line)) {
        return FAILED;
    }
    log_file->flush();
    return OK;
}

} // namespace godot
