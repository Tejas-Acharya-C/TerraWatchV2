import type { ImageryAcquisition } from './api'

export function getUsablePixelFraction(item: ImageryAcquisition): number {
  if (item.quality_metrics && typeof item.quality_metrics === 'object') {
    if (typeof item.quality_metrics.usable_percentage === 'number') {
      return item.quality_metrics.usable_percentage / 100
    }
    if (typeof item.quality_metrics.usable_pixel_fraction === 'number') {
      return item.quality_metrics.usable_pixel_fraction
    }
    if (typeof item.quality_metrics.usable_pixels === 'number') {
      if (item.quality_metrics.usable_pixels <= 0) return 0
      if (typeof item.quality_metrics.total_pixels === 'number' && item.quality_metrics.total_pixels > 0) {
        return item.quality_metrics.usable_pixels / item.quality_metrics.total_pixels
      }
    }
  }
  return 1.0
}

export function isTemporalEligible(item: ImageryAcquisition): boolean {
  if (!item.observation_state || item.observation_state !== 'usable') {
    return false
  }
  const fraction = getUsablePixelFraction(item)
  return fraction > 0
}

export function getTemporalIneligibleReason(item: ImageryAcquisition): string {
  if (item.observation_state === 'valid_unusable') {
    if (item.quality_reason && item.quality_reason !== 'quality_policy_passed') {
      return item.quality_reason.replace(/_/g, ' ')
    }
    return 'valid but unusable quality'
  }
  if (item.observation_state === 'failed') {
    return 'quality assessment failed'
  }
  if (item.observation_state === 'legacy_unassessed') {
    return 'legacy unassessed quality'
  }
  if (item.observation_state && item.observation_state !== 'usable') {
    return `${item.observation_state}`.replace(/_/g, ' ')
  }
  const fraction = getUsablePixelFraction(item)
  if (fraction <= 0) {
    return 'zero usable pixels'
  }
  return 'unavailable'
}
