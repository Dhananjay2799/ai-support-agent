"use client";

import { useEffect, useRef } from "react";

const BARS = 40;

export type Speaker = "agent" | "caller" | null;

/**
 * Live audio bars: the agent above the center line, the caller below it.
 * Reports who is audible so the panel can say "Listening" or "Speaking".
 */
export function Waveform({
  mic,
  agent,
  onSpeaker,
}: {
  mic: MediaStream | null;
  agent: MediaStream | null;
  onSpeaker?: (speaker: Speaker) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const speakerRef = useRef<Speaker>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || (!mic && !agent)) return;
    const context = new AudioContext();
    const analyse = (stream: MediaStream | null) => {
      if (!stream || stream.getAudioTracks().length === 0) return null;
      const analyser = context.createAnalyser();
      analyser.fftSize = 128;
      analyser.smoothingTimeConstant = 0.75;
      context.createMediaStreamSource(stream).connect(analyser);
      return { analyser, bins: new Uint8Array(analyser.frequencyBinCount) };
    };
    const callerSide = analyse(mic);
    const agentSide = analyse(agent);
    const styles = getComputedStyle(canvas);
    const agentColor = styles.getPropertyValue("--primary").trim() || "#4f46e5";
    const callerColor = "#10b981";
    const draw = canvas.getContext("2d");
    let frame = 0;

    const levels = (side: typeof callerSide) => {
      if (!side) return new Array<number>(BARS).fill(0);
      side.analyser.getByteFrequencyData(side.bins);
      const usable = Math.floor(side.bins.length * 0.7); // speech lives in the lower bins
      return Array.from({ length: BARS }, (_, i) => side.bins[Math.floor((i / BARS) * usable)] / 255);
    };

    const render = () => {
      frame = requestAnimationFrame(render);
      if (!draw) return;
      const { width, height } = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(width * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
      }
      draw.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw.clearRect(0, 0, width, height);

      const agentLevels = levels(agentSide);
      const callerLevels = levels(callerSide);
      const mid = height / 2;
      const gap = 2;
      const barWidth = (width - gap * (BARS - 1)) / BARS;
      for (let i = 0; i < BARS; i++) {
        const x = i * (barWidth + gap);
        const up = Math.max(1.5, agentLevels[i] * (mid - 2));
        const down = Math.max(1.5, callerLevels[i] * (mid - 2));
        draw.fillStyle = agentColor;
        draw.fillRect(x, mid - up, barWidth, up);
        draw.fillStyle = callerColor;
        draw.fillRect(x, mid, barWidth, down);
      }

      const average = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;
      const agentLevel = average(agentLevels);
      const callerLevel = average(callerLevels);
      const speaker: Speaker =
        agentLevel > 0.08 && agentLevel >= callerLevel ? "agent" : callerLevel > 0.08 ? "caller" : null;
      if (speaker !== speakerRef.current) {
        speakerRef.current = speaker;
        onSpeaker?.(speaker);
      }
    };
    render();

    return () => {
      cancelAnimationFrame(frame);
      void context.close();
    };
  }, [mic, agent, onSpeaker]);

  return <canvas ref={canvasRef} className="h-14 w-full" aria-hidden />;
}
