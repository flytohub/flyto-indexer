# Covers RUBY_SEND_METHOD
class Widget
  def invoke(action)
    @obj.send(params[:method])  # RUBY_SEND_METHOD
  end
end
