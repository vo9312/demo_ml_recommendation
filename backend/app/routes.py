from app import app
from app.models import MovieSchema, Movie
from app.top_rate_for_user import TopRateMovieForUser
from app.utils import load_pickle, InvalidUsage,\
    retrive_small_movie_metadata, compute_cosine_similarity,\
    convert_int, train_model, get_high_rating_movies

from flask import abort, g, jsonify, request, Response
import pandas as pd

movie_schema = MovieSchema()


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


@app.route('/api/top-ten')
def get_top_ten():
    credits = pd.read_csv("ml_data/credits.csv")
    keywords = pd.read_csv("ml_data/keywords.csv")
    movies = pd.read_csv("ml_data/movies_metadata.csv")
    indecies = movies[(movies.adult != 'True') &
                      (movies.adult != 'False')].index
    movies.drop(indecies, inplace=True)
    credits['id'] = credits['id'].astype('int')
    keywords['id'] = keywords['id'].astype('int')
    movies['id'] = movies['id'].astype('int')

    vote_counts = movies[movies['vote_count'].notnull()
                         ]['vote_count'].astype('int')
    m = vote_counts.quantile(0.95)
    vote_averages = movies[movies['vote_average'].notnull(
    )]['vote_average'].astype('float')
    C = vote_averages.mean()

    def weighted_rating(x):
        v = x['vote_count']
        R = x['vote_average']
        return (v/(v+m) * R) + (m/(m+v) * C)

    movies['score'] = movies.apply(weighted_rating, axis=1)
    movies = get_high_rating_movies(movies, 10)

    return Response(movies.to_json(orient="records"),
                    mimetype='application/json')


@app.route('/api/top-ten-similar/<movie_id>')
def get_top_ten_similar(movie_id):
    # return top 10 movies' movie_ids
    if movie_id.isdigit():
        try:
            model2 = load_pickle('ml_models/model2.pickle')
            ret = {
                'status': 'success',
                'top-ten': model2.get_recommendations(int(movie_id))
                .index.tolist()
            }
            return jsonify(ret)
        except Exception as e:
            raise InvalidUsage(
                'Have error when get top similars: ' + str(e), status_code=502)
    else:
        raise InvalidUsage('Need movie id', status_code=400)


@app.route('/api/estimated_rate/<user_id>/<movie_id>')
def get_estimated_rate(user_id, movie_id):
    user_id = convert_int(user_id)
    movie_id = convert_int(movie_id)
    model = train_model()
    predict = model.predict(user_id, movie_id)
    return jsonify(predict)


@app.route('/api/top-ten-rate', methods=['GET'])
def get_top_ten_rate_of_user():
    # return top 10 movies' with user_id
    user_id = int(request.args['user_id'])

    topRateMovieForUser = TopRateMovieForUser(
        'ml_data/', 'ratings_small.csv', 'ml_models/',
        'top-user-movie-ratings-small.pkl')

    res = topRateMovieForUser.get_top_ten_rate_of_user(user_id)
    if type(res) == str:
        abort(404, description="User id not found")
    else:
        return jsonify(topRateMovieForUser.get_top_ten_rate_of_user(user_id))


@app.route('/api/movies/<movie_id>')
def get_movie_by_id(movie_id):
    movie = Movie.query.filter(Movie.id == movie_id).one_or_none()
    if movie is None:
        abort(404, description="Resource not found")
    else:
        return movie_schema.dump(movie)


@app.route('/api/movies')
def get_movies_by_ids():
    movieIds = request.args.getlist('movieIds')
    movies = Movie.query.filter(Movie.id.in_(movieIds)).all()
    if len(movies) is None:
        abort(404, description="Resource not found")
    else:
        return movie_schema.jsonify(movies, many=True)


@app.route('/api/suggested_movies/<user_id>/<title>')
def hybrid(user_id, title):
    smd = retrive_small_movie_metadata()
    cosine_sim = compute_cosine_similarity(smd)
    model = train_model()
    user_id = convert_int(user_id)

    # The similar movies
    smd = smd.reset_index(drop=True)
    indices = pd.Series(smd.index, index=smd['title'])
    idx = indices[title]

    if not isinstance(idx.tolist(), int):
        idx = idx.values[0]
    sim_scores = list(enumerate(cosine_sim[int(idx)]))
    sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)
    sim_scores = sim_scores[1:11]
    movie_indices = [i[0] for i in sim_scores]
    # movie_indices

    # Compute the rating for each movie
    links_small_file = 'ml_data/links_small.csv'
    id_map = pd.read_csv(links_small_file)[['movieId', 'tmdbId']]
    id_map = id_map[id_map['tmdbId'].notnull()]
    id_map['tmdbId'] = id_map['tmdbId'].apply(convert_int)
    id_map.columns = ['movieId', 'id']
    id_map = id_map.merge(smd[['title', 'id']], on='id')
    indices_map = id_map.set_index('id')

    movies = smd.iloc[movie_indices][['title', 'id']]
    movies['est'] = movies['id'].apply(lambda x: model.predict(
        user_id, indices_map.loc[x]['movieId']).est)
    movies = movies.sort_values('est', ascending=False)

    return Response(movies.to_json(orient="records"),
                    mimetype='application/json')


@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response
