/**
 * Geohash encoding, for the discovery search binding's convenience form.
 *
 * §3.3.0 defines `GET /.well-known/spatialdds/search?geohash={geohash}` as the
 * form a client uses when all it has is a position — it is what a Geospatial
 * DNS-SD `muri` carries. The bridge and the conformance harness both decode
 * geohashes; nothing in the repo encoded one, because until now no client
 * started from a latitude and longitude and asked who was nearby.
 *
 * Standard base32 geohash: bits alternate longitude, latitude, halving the
 * remaining range each time, five bits per character.
 */

const BASE32 = '0123456789bcdefghjkmnpqrstuvwxyz';

/** §3.3.0 accepts 3–7 characters. */
export const MIN_PRECISION = 3;
export const MAX_PRECISION = 7;

/**
 * Precision 5 is ~4.9 km square. The demo's VPS covers roughly 2.9 x 2.2 km,
 * so a precision-5 cell around the user overlaps it from anywhere inside, and
 * from a little way outside too — which is the forgiving behaviour you want
 * when the position being encoded is a *prior*, i.e. the thing the client is
 * asking the VPS to correct.
 */
export const DEFAULT_PRECISION = 5;

export function geohashEncode(
  latDeg: number,
  lonDeg: number,
  precision: number = DEFAULT_PRECISION
): string {
  if (!Number.isFinite(latDeg) || !Number.isFinite(lonDeg)) {
    throw new RangeError('geohashEncode requires finite coordinates');
  }
  if (latDeg < -90 || latDeg > 90) {
    throw new RangeError(`latitude out of range: ${latDeg}`);
  }
  if (lonDeg < -180 || lonDeg > 180) {
    throw new RangeError(`longitude out of range: ${lonDeg}`);
  }
  const chars = Math.trunc(precision);
  if (chars < MIN_PRECISION || chars > MAX_PRECISION) {
    throw new RangeError(
      `geohash precision must be ${MIN_PRECISION}-${MAX_PRECISION} (§3.3.0), got ${precision}`
    );
  }

  let latMin = -90;
  let latMax = 90;
  let lonMin = -180;
  let lonMax = 180;

  let hash = '';
  let bits = 0;
  let bitCount = 0;
  let longitudeTurn = true;

  while (hash.length < chars) {
    if (longitudeTurn) {
      const mid = (lonMin + lonMax) / 2;
      if (lonDeg >= mid) {
        bits = (bits << 1) | 1;
        lonMin = mid;
      } else {
        bits = bits << 1;
        lonMax = mid;
      }
    } else {
      const mid = (latMin + latMax) / 2;
      if (latDeg >= mid) {
        bits = (bits << 1) | 1;
        latMin = mid;
      } else {
        bits = bits << 1;
        latMax = mid;
      }
    }
    longitudeTurn = !longitudeTurn;

    if (++bitCount === 5) {
      hash += BASE32[bits];
      bits = 0;
      bitCount = 0;
    }
  }

  return hash;
}
