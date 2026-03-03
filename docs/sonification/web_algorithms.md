# Depth-Map Sonification Algorithms for Web App

This project can implement real-time sonification in the browser with HTML5 Web Audio API (stereo).

## Implemented in `/app`

1. `Nearest Ping`
- Uses the nearest valid zone.
- Stereo pan from zone column.
- Pitch and gain mapped from depth.
- Good for quick obstacle warning.

2. `Sweep Melody`
- Scans columns left-to-right.
- In each column, sonifies nearest valid zone.
- Stereo pan by column, pitch from depth + row offset.
- Good for scene structure learning.

3. `Stereo Bands`
- Splits scene into left/center/right bands.
- Sonifies nearest obstacle in each band with separate stereo placement.
- Good balance between detail and low cognitive load.

4. `Depth Triad`
- Uses 3 nearest obstacles.
- Plays a short chord with different intervals.
- Each tone keeps stereo pan from obstacle direction.
- Good for compact multi-obstacle awareness.

## Additional algorithms feasible in browser

1. `Beep Repetition Rate (BRR)`
- Depth encoded as repetition speed (nearer = faster).
- Can run as one global pulse or per stereo side.

2. `U-Depth Histogram Sonification`
- Build horizontal depth histogram.
- Map bin depth to pitch, bin magnitude to gain.

3. `Noise + Filter Brightness`
- Use filtered noise bursts.
- Near obstacles produce brighter/noisier timbre.

4. `Semantic Cue Layer`
- Add simple earcons for floor/wall/opening when CV/heuristics available.

5. `Binaural/HRTF Rendering`
- Replace simple stereo panning with binaural convolution.
- Better externalization but higher CPU cost.
