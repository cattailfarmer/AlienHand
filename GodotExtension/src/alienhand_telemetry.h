#pragma once

#include <godot_cpp/classes/file_access.hpp>
#include <godot_cpp/classes/node.hpp>
#include <godot_cpp/variant/dictionary.hpp>
#include <godot_cpp/variant/string.hpp>

namespace godot {

class AlienHandTelemetry : public Node {
    GDCLASS(AlienHandTelemetry, Node)

    Ref<FileAccess> log_file;
    String log_path;
    String session_id = "godot";
    int frame_count = 0;

protected:
    static void _bind_methods();

public:
    AlienHandTelemetry() = default;
    ~AlienHandTelemetry() override;

    Error start_recording(const String &path, const String &session = "godot");
    void stop_recording();
    bool is_recording() const;

    void set_session_id(const String &session);
    String get_session_id() const;
    String get_log_path() const;
    int get_frame_count() const;

    Dictionary make_frame_event(const Dictionary &state) const;
    Dictionary make_event(const String &kind, const Dictionary &payload = Dictionary()) const;
    Error record_frame(const Dictionary &state);
    Error log_event(const String &kind, const Dictionary &payload = Dictionary());

private:
    Error write_jsonl(const Dictionary &event);
};

} // namespace godot
