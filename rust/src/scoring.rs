use pyo3::prelude::*;

/// Batch-compute project scores.
///
/// Each input row is `(avg_online, comments, hearts, thumbs, verified_multiplier)`.
/// Returns `Vec<(score, online_points, comment_points, heart_points, thumb_points, verified_bonus)>`.
#[pyfunction]
pub fn compute_scores_batch(
    rows: Vec<(f64, i64, i64, i64, f64)>,
    weight_online: f64,
    weight_comments: f64,
    weight_hearts: f64,
    weight_thumbs: f64,
) -> Vec<(f64, f64, f64, f64, f64, f64)> {
    rows.iter()
        .map(
            |&(avg_online, comments, hearts, thumbs, verified_multiplier)| {
                let online_points = (avg_online.max(0.0)).ln_1p() * weight_online;
                let comment_points = ((comments.max(0)) as f64).ln_1p() * weight_comments;
                let heart_points = ((hearts.max(0)) as f64).ln_1p() * weight_hearts;
                let thumb_points = ((thumbs.max(0)) as f64).ln_1p() * weight_thumbs;

                let base_score = online_points + comment_points + heart_points + thumb_points;
                let score = base_score * verified_multiplier;
                let verified_bonus = score - base_score;

                (
                    score,
                    online_points,
                    comment_points,
                    heart_points,
                    thumb_points,
                    verified_bonus,
                )
            },
        )
        .collect()
}
