//! User settings, persisted as JSON in the app config directory.

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct Settings {
    /// How the learner's voice becomes text: audio (Gemma), whisper, kyutai.
    pub stt: String,
    /// How a turn ends: ptt, vad, semantic.
    pub turn: String,
    /// Tutor voice: kokoro, kyutai.
    pub tts: String,
    /// Playback speed of the tutor (UI range 0.75 - 1.25).
    pub speed: f64,
    /// VAD mode: seconds of silence that end the turn.
    pub silence: f64,
    /// Program text: fr or en. The tutor always speaks French.
    pub ui_lang: String,
    /// Translate and extract vocabulary in the background after each turn.
    pub prefetch: bool,
    /// Speak the greeting when the engine is ready.
    pub greet: bool,
    /// Developer options: model choice and tuning.
    pub dev: DevSettings,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct DevSettings {
    /// Model alias (gemma-e4b, gemma-e2b, gemma-12b, qwen-3b, qwen-7b, mistral)
    /// or a Hugging Face MLX repo.
    pub model: String,
    /// Context window in tokens (older turns are dropped when it is full).
    pub max_context: u32,
    /// None = the model's default (1.0 for Gemma, 0.7 otherwise).
    pub temperature: Option<f64>,
    /// None = model default (Gemma: 0.95 / 64; off for other models).
    pub top_p: Option<f64>,
    pub top_k: Option<u32>,
    /// Reply length cap in tokens.
    pub max_tokens: u32,
    /// --turn semantic: Kyutai end-of-turn threshold (higher waits longer).
    pub eot_threshold: f64,
    /// --stt whisper model.
    pub whisper_model: String,
    /// Kyutai quantization: 8, 4 or 0 (= bf16).
    pub kyutai_bits: u8,
    pub kyutai_stt_bits: u8,
    /// Model loading details and Hugging Face output in the engine log.
    pub verbose: bool,
}

impl Default for DevSettings {
    fn default() -> Self {
        Self {
            model: "gemma-e4b".into(),
            max_context: 16384,
            temperature: None,
            top_p: None,
            top_k: None,
            max_tokens: 220,
            eot_threshold: 0.5,
            whisper_model: "mlx-community/whisper-base-mlx".into(),
            kyutai_bits: 8,
            kyutai_stt_bits: 8,
            verbose: false,
        }
    }
}

impl DevSettings {
    fn sanitized(mut self) -> Self {
        let d = DevSettings::default();
        if self.model.trim().is_empty() {
            self.model = d.model;
        }
        if self.whisper_model.trim().is_empty() {
            self.whisper_model = d.whisper_model;
        }
        self.max_context = self.max_context.clamp(2048, 131072);
        self.temperature = self.temperature.map(|t| t.clamp(0.0, 2.0));
        self.top_p = self.top_p.map(|p| p.clamp(0.05, 1.0));
        self.top_k = self.top_k.map(|k| k.clamp(1, 1000));
        self.max_tokens = self.max_tokens.clamp(40, 1024);
        self.eot_threshold = self.eot_threshold.clamp(0.05, 0.99);
        for bits in [&mut self.kyutai_bits, &mut self.kyutai_stt_bits] {
            if ![0, 4, 8].contains(bits) {
                *bits = 8;
            }
        }
        self
    }
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            stt: "audio".into(),
            turn: "ptt".into(),
            tts: "kokoro".into(),
            speed: 0.92,
            silence: 1.2,
            ui_lang: "fr".into(),
            prefetch: true,
            greet: true,
            dev: DevSettings::default(),
        }
    }
}

impl Settings {
    pub fn load(path: &Path) -> Self {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str::<Settings>(&s).ok())
            .unwrap_or_default()
            .sanitized()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())
    }

    /// Clamp values. Memory limits come from the engine (Metal's recommended
    /// working set), so they are enforced in the UI, not here.
    pub fn sanitized(mut self) -> Self {
        let d = Settings::default();
        if !["audio", "whisper", "kyutai"].contains(&self.stt.as_str()) {
            self.stt = d.stt.clone();
        }
        if !["ptt", "vad", "semantic"].contains(&self.turn.as_str()) {
            self.turn = d.turn.clone();
        }
        if !["kokoro", "kyutai"].contains(&self.tts.as_str()) {
            self.tts = d.tts.clone();
        }
        if !["fr", "en"].contains(&self.ui_lang.as_str()) {
            self.ui_lang = d.ui_lang.clone();
        }
        self.speed = self.speed.clamp(0.75, 1.25);
        self.silence = self.silence.clamp(0.6, 3.0);
        self.dev = self.dev.sanitized();
        self
    }

    /// True if going from `self` to `other` needs a sidecar restart (models
    /// change). Turn mode and speed are changed live.
    pub fn needs_restart(&self, other: &Settings) -> bool {
        self.stt != other.stt
            || self.tts != other.tts
            || self.silence != other.silence
            || self.prefetch != other.prefetch
            || self.dev.model != other.dev.model
            || self.dev.whisper_model != other.dev.whisper_model
            || self.dev.kyutai_bits != other.dev.kyutai_bits
            || self.dev.kyutai_stt_bits != other.dev.kyutai_stt_bits
            || self.dev.verbose != other.dev.verbose
    }

    pub fn engine_args(&self) -> Vec<String> {
        let mut args = vec![
            "--stt".into(),
            self.stt.clone(),
            "--turn".into(),
            self.turn.clone(),
            "--tts".into(),
            self.tts.clone(),
            "--speed".into(),
            format!("{:.2}", self.speed),
            "--silence".into(),
            format!("{:.2}", self.silence),
        ];
        let d = &self.dev;
        for (flag, value) in [
            ("--model", d.model.clone()),
            ("--max-context", d.max_context.to_string()),
            ("--max-tokens", d.max_tokens.to_string()),
            ("--eot-threshold", format!("{:.2}", d.eot_threshold)),
            ("--whisper-model", d.whisper_model.clone()),
            ("--kyutai-bits", d.kyutai_bits.to_string()),
            ("--kyutai-stt-bits", d.kyutai_stt_bits.to_string()),
        ] {
            args.push(flag.into());
            args.push(value);
        }
        if let Some(t) = d.temperature {
            args.push("--temperature".into());
            args.push(format!("{t:.2}"));
        }
        if let Some(p) = d.top_p {
            args.push("--top-p".into());
            args.push(format!("{p:.2}"));
        }
        if let Some(k) = d.top_k {
            args.push("--top-k".into());
            args.push(k.to_string());
        }
        if d.verbose {
            args.push("--verbose".into());
        }
        if !self.prefetch {
            args.push("--no-prefetch".into());
        }
        if !self.greet {
            args.push("--no-greet".into());
        }
        args
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_any_model_combination() {
        let s = Settings { turn: "semantic".into(), tts: "kyutai".into(), ..Default::default() }.sanitized();
        assert_eq!(s.tts, "kyutai");
        let s = Settings { stt: "bogus".into(), ..Default::default() }.sanitized();
        assert_eq!(s.stt, "audio");
    }

    #[test]
    fn clamps_and_restart_rules() {
        let s = Settings { speed: 3.0, ..Default::default() }.sanitized();
        assert_eq!(s.speed, 1.25);
        let a = Settings::default();
        let b = Settings { turn: "vad".into(), speed: 0.8, ..Default::default() };
        assert!(!a.needs_restart(&b));
        let c = Settings { stt: "whisper".into(), ..Default::default() };
        assert!(a.needs_restart(&c));
        // Sampling and context are live; the model is not.
        let mut d = Settings::default();
        d.dev.temperature = Some(0.4);
        d.dev.max_context = 8192;
        assert!(!a.needs_restart(&d));
        d.dev.model = "qwen-3b".into();
        assert!(a.needs_restart(&d));
    }

    #[test]
    fn old_settings_files_still_load() {
        let s: Settings = serde_json::from_str(r#"{"stt":"whisper","speed":1.0}"#).unwrap();
        assert_eq!(s.stt, "whisper");
        assert_eq!(s.dev, DevSettings::default());
        let args = s.sanitized().engine_args();
        assert!(args.windows(2).any(|w| w[0] == "--model" && w[1] == "gemma-e4b"));
        assert!(!args.contains(&"--temperature".to_string()));
    }
}
