use std::ffi::c_void;
use std::fs::File;
use std::io::{self, Read, Write};
use std::os::windows::io::FromRawHandle;

use mxc_sdk::v1::{self, ErrorCode, SpawnStdio, WaitResult};
use serde::{Deserialize, Serialize};
use windows::Win32::Foundation::{HANDLE, HANDLE_FLAG_INHERIT, HANDLE_FLAGS, SetHandleInformation};
use windows::Win32::Storage::FileSystem::{FILE_TYPE_PIPE, GetFileType};
use windows::Win32::System::Console::{
    GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
};

const HELPER_FAILURE: i32 = 1;
const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
const MXC_VERSION: &str = "1.1.0";

// MXC chooses its telemetry provider at build time. Require a deliberately
// unrouted provider for any release binary, even outside the bundle builder.
#[cfg(not(debug_assertions))]
const _: () = assert!(
    matches!(option_env!("MXC_TELEMETRY_COMPONENT_PATH"), Some(value) if value.is_empty()),
    "MXC_TELEMETRY_COMPONENT_PATH must be set to an empty string for release builds"
);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Probe {
    helper_version: &'static str,
    mxc_version: &'static str,
    base_container: bool,
    fs_deny: bool,
    loopback_server: bool,
}

#[derive(Deserialize)]
struct RequestHeader<'a> {
    version: &'a str,
    containment: &'a str,
}

#[derive(Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
enum Status {
    Failed {
        code: FailureCode,
    },
    Started,
    Exited {
        #[serde(rename = "exitCode")]
        exit_code: u32,
    },
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum FailureCode {
    RequestInvalid,
    BackendUnavailable,
    PolicyRejected,
    LaunchFailed,
}

pub fn main() -> i32 {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [command] if command == "probe" => probe().map_or(HELPER_FAILURE, |()| 0),
        [command, request_flag, request, status_flag, status]
            if command == "run"
                && request_flag == "--request-handle"
                && status_flag == "--status-handle" =>
        {
            let Some((request, status)) = control_pipes(request, status) else {
                return HELPER_FAILURE;
            };
            run(request, status).unwrap_or(HELPER_FAILURE)
        }
        _ => HELPER_FAILURE,
    }
}

fn probe() -> io::Result<()> {
    let result = v1::probe(None).map_err(|_| io::Error::other("MXC probe failed"))?;
    let facts = result.probes;
    write_json_line(
        &mut io::stdout().lock(),
        &Probe {
            helper_version: env!("CARGO_PKG_VERSION"),
            mxc_version: MXC_VERSION,
            base_container: result.tier == Some("base-container"),
            fs_deny: facts.base_container_supports_deny_paths,
            loopback_server: facts.base_container_supports_ingress_host_loopback_allow,
        },
    )
}

fn control_pipes(request: &str, status: &str) -> Option<(File, File)> {
    let request = HANDLE(request.parse::<usize>().ok()? as *mut c_void);
    let status = HANDLE(status.parse::<usize>().ok()? as *mut c_void);
    if request == status || request.is_invalid() || status.is_invalid() {
        return None;
    }
    for std in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
        if let Ok(handle) = unsafe { GetStdHandle(std) }
            && (request == handle || status == handle)
        {
            return None;
        }
    }
    for handle in [request, status] {
        // Inherited control pipes belong exclusively to this launcher. MXC
        // passes only the standard handles on to the contained process.
        if unsafe { GetFileType(handle) } != FILE_TYPE_PIPE
            || unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT.0, HANDLE_FLAGS(0)) }
                .is_err()
        {
            return None;
        }
    }
    // The parent transferred ownership of two distinct pipe handles.
    Some(unsafe {
        (
            File::from_raw_handle(request.0),
            File::from_raw_handle(status.0),
        )
    })
}

fn read_request(request: impl Read) -> Option<String> {
    let mut json = String::new();
    request
        .take(MAX_REQUEST_BYTES + 1)
        .read_to_string(&mut json)
        .ok()?;
    if json.len() as u64 > MAX_REQUEST_BYTES {
        return None;
    }
    let header: RequestHeader<'_> = serde_json::from_str(&json).ok()?;
    (header.version == "1.0.0" && header.containment == "processcontainer").then_some(json)
}

fn run(request: File, mut status: File) -> io::Result<i32> {
    let Some(json) = read_request(request) else {
        return failed(&mut status, FailureCode::RequestInvalid);
    };

    let mut options = v1::dev::SpawnExtendedJsonOptions::default();
    options.stdio = SpawnStdio::Inherit;
    let mut child = match v1::dev::spawn_with_extended_options_json(&json, options) {
        Ok(child) => child,
        Err(error) => {
            // In the pinned ProcessContainer backend, spawn can return an
            // error only before the workload's main thread is resumed.
            // Never forward the SDK diagnostics, which can contain request data.
            let code = match error.code {
                ErrorCode::MalformedRequest
                | ErrorCode::UnsupportedContainment
                | ErrorCode::UnsupportedPhase => FailureCode::RequestInvalid,
                ErrorCode::BackendUnavailable => FailureCode::BackendUnavailable,
                ErrorCode::PolicyValidation => FailureCode::PolicyRejected,
                ErrorCode::BackendError => FailureCode::LaunchFailed,
                _ => return Ok(HELPER_FAILURE),
            };
            return failed(&mut status, code);
        }
    };
    write_json_line(&mut status, &Status::Started)?;

    let outcome = child.wait();
    drop(child);
    // Once started, only report a verified exit. The Runtime owns the deadline
    // and records an unknown result whenever it cannot confirm completion.
    let WaitResult::Exited(code) = outcome? else {
        return Ok(HELPER_FAILURE);
    };
    write_json_line(
        &mut status,
        &Status::Exited {
            exit_code: code as u32,
        },
    )?;
    Ok(code)
}

fn failed(status: &mut File, code: FailureCode) -> io::Result<i32> {
    write_json_line(status, &Status::Failed { code })?;
    Ok(HELPER_FAILURE)
}

fn write_json_line(out: &mut impl Write, value: &impl Serialize) -> io::Result<()> {
    let mut line = serde_json::to_vec(value)?;
    line.push(b'\n');
    out.write_all(&line)?;
    out.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID: &str = r#"{"version":"1.0.0","containment":"processcontainer","process":{"commandLine":"cmd /c exit 0"}}"#;

    #[test]
    fn accepts_only_the_pinned_process_container_contract() {
        assert_eq!(read_request(VALID.as_bytes()).as_deref(), Some(VALID));
        for bad in [
            r#"{"version":"1.1.0-alpha","containment":"processcontainer"}"#,
            r#"{"version":"1.0.0","containment":"isolation_session"}"#,
            r#"{"version":"1.0.0"}"#,
            r#"{"version":"1.0.0","containment":"processcontainer","containment":"isolation_session"}"#,
        ] {
            assert_eq!(read_request(bad.as_bytes()), None);
        }
    }

    #[test]
    fn caps_request_bytes_and_requires_utf8() {
        let mut bytes = VALID.as_bytes().to_vec();
        bytes.resize(MAX_REQUEST_BYTES as usize, b' ');
        assert!(read_request(bytes.as_slice()).is_some());
        bytes.push(b' ');
        assert!(read_request(bytes.as_slice()).is_none());
        assert!(read_request(&[0xff][..]).is_none());
    }

    #[test]
    fn status_lines_have_no_workload_output_or_diagnostic_fields() {
        let mut out = Vec::new();
        write_json_line(&mut out, &Status::Started).unwrap();
        write_json_line(
            &mut out,
            &Status::Exited {
                exit_code: u32::MAX,
            },
        )
        .unwrap();
        write_json_line(
            &mut out,
            &Status::Failed {
                code: FailureCode::PolicyRejected,
            },
        )
        .unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "{\"event\":\"started\"}\n{\"event\":\"exited\",\"exitCode\":4294967295}\n{\"event\":\"failed\",\"code\":\"policy_rejected\"}\n",
        );
    }
}
