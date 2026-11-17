/**
 * Static offline HTML fixtures for Chrome/Firefox simulated pages.
 */

export const MOCK_MOVIE_PAGE = `
<!DOCTYPE html>
<html>
<head>
  <title>Watch Inception - MockStream</title>
  <meta name="media-type" content="movie" />
</head>
<body>
  <div class="player-container">
    <h1 data-testid="media-title">Inception</h1>
    <span data-testid="media-year">2010</span>
    <video src="blob:mock" currentTime="3600" duration="8880"></video>
  </div>
</body>
</html>
`;

export const MOCK_SERIES_EPISODE_PAGE = `
<!DOCTYPE html>
<html>
<head>
  <title>Breaking Bad S05:E14 Ozymandias - MockStream</title>
  <meta name="media-type" content="tv" />
</head>
<body>
  <div class="player-container">
    <h1 class="media-title">Breaking Bad</h1>
    <div class="media-year">2013</div>
    <div class="season-episode">S05:E14</div>
    <div class="episode-title">Ozymandias</div>
    <video src="blob:mock" currentTime="1200" duration="2820"></video>
  </div>
</body>
</html>
`;

export const MOCK_EMPTY_PAGE = `
<!DOCTYPE html>
<html>
<head>
  <title>MockStream Catalog</title>
</head>
<body>
  <div>Browse titles</div>
</body>
</html>
`;

export const MOCK_MALFORMED_PAGE = `
<!DOCTYPE html>
<html>
<head>
  <title>Error</title>
</head>
<body>
  <h1 data-testid="media-title">    </h1>
  <video></video>
</body>
</html>
`;
