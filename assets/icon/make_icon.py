import math, sys
out = sys.argv[1]
cx = cy = 512
# Kettenblatt: 32 Zähne
N, r_out, r_in = 32, 330, 300
pts = []
for i in range(N):
    a0 = 2*math.pi*i/N
    step = 2*math.pi/N
    for frac, r in ((0.0, r_in), (0.18, r_out), (0.42, r_out), (0.6, r_in)):
        a = a0 + frac*step - math.pi/2
        pts.append((cx + r*math.cos(a), cy + r*math.sin(a)))
teeth = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
# geglättete Kurven (Catmull-Rom -> Bezier)
def smooth(pts):
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts)-1):
        p0 = pts[max(i-1,0)]; p1 = pts[i]; p2 = pts[i+1]; p3 = pts[min(i+2,len(pts)-1)]
        c1 = (p1[0]+(p2[0]-p0[0])/6, p1[1]+(p2[1]-p0[1])/6)
        c2 = (p2[0]-(p3[0]-p1[0])/6, p2[1]-(p3[1]-p1[1])/6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d
xs = [240 + i*54.4 for i in range(11)]
rider_y = [560, 520, 470, 505, 430, 470, 520, 455, 400, 470, 520]
motor_y = [610, 590, 560, 575, 535, 560, 590, 555, 520, 565, 600]
rider = smooth(list(zip(xs, rider_y)))
motor_line = smooth(list(zip(xs, motor_y)))
motor = motor_line + " L800,800 L230,800 Z"
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1B2330"/>
      <stop offset="1" stop-color="#0A0E14"/>
    </linearGradient>
    <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#3FE0C5"/>
      <stop offset="1" stop-color="#1A9E8C"/>
    </linearGradient>
    <clipPath id="inner"><circle cx="{cx}" cy="{cy}" r="262"/></clipPath>
  </defs>
  <rect width="1024" height="1024" fill="url(#bg)"/>
  <path d="{teeth}" fill="url(#ring)"/>
  <circle cx="{cx}" cy="{cy}" r="262" fill="#0F151D"/>
  <g clip-path="url(#inner)" fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g stroke="#2A3442" stroke-width="4">
      <line x1="250" y1="440" x2="774" y2="440"/>
      <line x1="250" y1="540" x2="774" y2="540"/>
      <line x1="250" y1="640" x2="774" y2="640"/>
    </g>
    <path d="{motor}" fill="#3FE0C5" opacity="0.22" stroke="none"/>
    <path d="{motor_line}" stroke="#3FE0C5" stroke-width="16" opacity="0.9"/>
    <path d="{rider}" stroke="#FF8A2B" stroke-width="28"/>
  </g>
  <circle cx="{cx}" cy="{cy}" r="262" fill="none" stroke="#3FE0C5" stroke-width="10" opacity="0.35"/>
</svg>
'''
open(out, "w").write(svg)
