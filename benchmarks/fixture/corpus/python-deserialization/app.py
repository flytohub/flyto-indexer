import pickle


def load_state(request):
    blob = request.data
    return pickle.loads(blob)
