/**
 * Phase 1 recording format.
 *
 * `tools/record.py` delta-encodes food between frames, because a full 600
 * pellet list on every one of ~540 frames dwarfs everything else in the file.
 * This module is the only thing that knows about that encoding: it hands out
 * plain section 4 `state` objects, so the renderer never sees the compression.
 *
 * No DOM access in here, deliberately, so it can be exercised outside a browser.
 */

export class RecordingCursor {
  constructor(recording) {
    this.recording = recording;
    this.map = new Map();
    this.at = -1;
  }

  get frameCount() {
    return this.recording.frames.length;
  }

  reset() {
    this.map.clear();
    this.at = -1;
  }

  /**
   * Live food list at `index`.
   *
   * Walking forward is cheap: each frame carries at most a couple of changes.
   * Seeking backwards replays from frame 0, which is a few hundred trivial map
   * operations and still far cheaper than caching a list per frame.
   */
  foodAt(index) {
    const frames = this.recording.frames;
    const target = Math.min(Math.max(index, 0), frames.length - 1);

    if (target < this.at) this.reset();
    if (this.at < 0) {
      for (const [id, x, y] of frames[0].food || []) this.map.set(id, { id, x, y });
      this.at = 0;
    }
    while (this.at < target) {
      this.at += 1;
      const frame = frames[this.at];
      for (const id of frame.food_removed || []) this.map.delete(id);
      for (const [id, x, y] of frame.food_added || []) this.map.set(id, { id, x, y });
    }
    return Array.from(this.map.values());
  }

  /** A section 4 `state` message for `index`. */
  stateAt(index) {
    const frames = this.recording.frames;
    const target = Math.min(Math.max(index, 0), frames.length - 1);
    return {
      type: "state",
      players: frames[target].players,
      food: this.foodAt(target),
    };
  }

  frameAt(index) {
    const frames = this.recording.frames;
    return frames[Math.min(Math.max(index, 0), frames.length - 1)];
  }

  /** Flattened `{ index, t, event }` list, for the event log. */
  events() {
    const entries = [];
    this.recording.frames.forEach((frame, index) => {
      for (const event of frame.events || []) entries.push({ index, t: frame.t, event });
    });
    return entries;
  }
}
